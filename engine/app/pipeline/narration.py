"""'narration' job: one WAV per shot (assets/voice/<shot>.wav) and "smart length":
if a line runs longer than its shot, the shot is lengthened to fit (+ a short breath).

params: voice (default af_heart), speed (default 0.85 for kids, else 1.0), shot_ids, force
"""
from __future__ import annotations

import math

from app.providers.kokoro_voice import DEFAULT_VOICE, KokoroVoiceProvider
from app.queue import JobContext, register
from app.render.ffmpeg import duration
from app.script.guards import is_kids
from app.script.schema import Script
from app.store.projects import ProjectStore

BREATH_S = 0.5     # silence after a line before the next shot's line
LEAD_IN_S = 0.25   # narration starts this long after its shot begins


KIDS_SPEED, DEFAULT_SPEED = 0.85, 1.0   # slower, clearer narration for young children


def provider_factory(ctx: JobContext, speed: float):
    return KokoroVoiceProvider(ctx.settings.models_dir / "kokoro", speed=speed)


def fit_duration(current: float, voice_s: float) -> float:
    """Shot length that fits its narration, rounded up to 0.1 s; never shorter than before."""
    need = LEAD_IN_S + voice_s + BREATH_S
    return max(current, math.ceil(need * 10) / 10)


@register("narration")
def narration_job(ctx: JobContext) -> dict:
    store = ProjectStore(ctx.settings.projects_dir)
    project = store.get(ctx.job.project_id or "")
    if "script" not in project.data:
        raise ValueError("project has no script yet")
    script = Script.model_validate(project.data["script"])
    voice = ctx.params.get("voice") or project.data.get("voice_name") or DEFAULT_VOICE
    wanted = set(ctx.params.get("shot_ids") or [s.id for s in script.shots])
    force = bool(ctx.params.get("force"))
    language = project.data.get("language", "English")
    kids = is_kids(project.data.get("audience", "children 4-8"))
    speed = float(ctx.params.get("speed") or project.data.get("voice_speed") or (KIDS_SPEED if kids else DEFAULT_SPEED))
    ctx.require_model("voice")
    provider = provider_factory(ctx, speed)
    ctx.cleanups.append(provider.unload)

    shots = [s for s in script.shots if s.id in wanted and s.narration.strip()]
    lengthened, made = [], []
    for i, shot in enumerate(shots):
        out = store.assets_dir(project.id) / "voice" / f"{shot.id}.wav"
        with ctx.step(f"voice-{shot.id}") as todo:
            old = project.data.get("voice", {}).get(shot.id, {})
            stale = old.get("text") not in (None, shot.narration) or (old and old.get("voice") != voice)
            if todo and (force or stale or not out.exists()):
                provider.speak(shot.narration, out, voice=voice, language=language)
                made.append(shot.id)
        ctx.progress((i + 1) / len(shots), f"narration {i + 1}/{len(shots)}")

    # Record durations and stretch shots that are too short for their line.
    project = store.get(project.id)
    script = Script.model_validate(project.data["script"])
    meta = project.data.setdefault("voice", {})
    for shot in script.shots:
        wav = store.assets_dir(project.id) / "voice" / f"{shot.id}.wav"
        if not wav.exists():
            continue
        secs = round(duration(wav), 3)
        meta[shot.id] = {"path": f"assets/voice/{shot.id}.wav", "seconds": secs, "voice": voice,
                         "speed": speed, "text": shot.narration}
        new = fit_duration(shot.duration_s, secs)
        if new != shot.duration_s:
            lengthened.append(f"{shot.id}: {shot.duration_s}→{new}s")
            shot.duration_s = new
    project.data["script"] = script.model_dump()
    store.save(project)
    return {"made": made, "lengthened": lengthened, "voice": voice, "speed": speed,
            "total_s": round(sum(s.duration_s for s in script.shots), 1)}
