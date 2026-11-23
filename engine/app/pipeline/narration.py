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
    if project.cast:
        return story_narration(ctx, store, project, script)
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


# ---- Story Builder: one WAV per line, each speaker in their own voice ------------------------
LINE_GAP_S = 0.25          # between two lines of one shot
LINE_RMS_DB = -22.0        # voices differ in loudness; bring every line to the same level


def _level(x, target_db: float = LINE_RMS_DB, peak: float = 0.9):
    import numpy as np
    loud = np.abs(x) > 0.01
    rms = float(np.sqrt(np.mean(x[loud] ** 2))) if loud.any() else 0.0
    if rms <= 0:
        return x
    gain = 10 ** (target_db / 20) / rms
    return (x * min(gain, peak / max(1e-6, float(np.abs(x).max())))).astype("float32")


def speaker_voice(speaker: str, project, cast: dict) -> dict:
    c = cast.get(speaker)
    v = c.voice.model_dump() if c else {"voice": DEFAULT_VOICE, "speed": KIDS_SPEED, "pitch": 0.0, **(project.narrator or {})}
    return {"voice": v["voice"], "speed": float(v["speed"]), "pitch": float(v.get("pitch") or 0.0)}


def story_narration(ctx: JobContext, store: ProjectStore, project, script: Script) -> dict:
    """assets/voice/<shot>-<n>.wav per line + assets/voice/<shot>.wav with all lines in order; the shot gets the
    length its lines need. Only lines whose text, speaker or voice changed are spoken again."""
    import numpy as np
    import soundfile as sf

    from app.audio import pitch_shift
    from app.pipeline.story import story_cast
    cast = {c.name: c for c in story_cast(ctx.settings, project)}
    wanted = set(ctx.params.get("shot_ids") or [s.id for s in script.shots])
    force = bool(ctx.params.get("force"))
    vdir = store.assets_dir(project.id) / "voice"
    todo = [(s, i, l) for s in script.shots if s.id in wanted for i, l in enumerate(s.spoken(), 1)]
    ctx.require_model("voice")
    made: list[str] = []
    old_meta = project.data.get("voice", {})
    provider = None
    for k, (shot, i, line) in enumerate(todo):
        v = speaker_voice(line.speaker, project, cast)
        key = {"text": line.text, "speaker": line.speaker, **v}
        out = vdir / f"{shot.id}-{i}.wav"
        prev = (old_meta.get(shot.id, {}).get("lines") or [])
        same = i <= len(prev) and {x: prev[i - 1].get(x) for x in key} == key
        with ctx.step(f"line-{shot.id}-{i}") as go:
            if go and (force or not same or not out.exists()):
                if provider is None:
                    provider = provider_factory(ctx, v["speed"])
                    ctx.cleanups.append(provider.unload)
                provider.speed = v["speed"]
                raw = out.with_name(f"{out.stem}-raw.wav")
                provider.speak(line.text, raw, voice=v["voice"], language=project.data.get("language", "English"))
                pitch_shift(raw, out, v["pitch"])
                raw.unlink(missing_ok=True)
                x, sr = sf.read(out, dtype="float32")
                sf.write(out, _level(x), sr, subtype="PCM_16")
                made.append(f"{shot.id}-{i}")
        ctx.progress((k + 1) / max(1, len(todo)), f"voices {k + 1}/{len(todo)} ({line.speaker})")

    project = store.get(project.id)
    script = Script.model_validate(project.data["script"])
    meta = project.data.setdefault("voice", {})
    lengths = []
    for shot in script.shots:
        lines = shot.spoken()
        files = [vdir / f"{shot.id}-{i}.wav" for i in range(1, len(lines) + 1)]
        if shot.id not in wanted:
            continue
        if not lines or not all(f.exists() for f in files):
            meta.pop(shot.id, None)
            continue
        parts, rows, t, sr = [], [], 0.0, 24000
        for i, (l, f) in enumerate(zip(lines, files)):
            x, sr = sf.read(f, dtype="float32")
            if i:
                parts.append(np.zeros(int(LINE_GAP_S * sr), dtype="float32"))
                t += LINE_GAP_S
            rows.append({"path": f"assets/voice/{f.name}", "start": round(t, 3), "seconds": round(len(x) / sr, 3),
                         "text": l.text, "speaker": l.speaker, **speaker_voice(l.speaker, project, cast)})
            parts.append(x)
            t += len(x) / sr
        sf.write(vdir / f"{shot.id}.wav", np.concatenate(parts), sr, subtype="PCM_16")
        meta[shot.id] = {"path": f"assets/voice/{shot.id}.wav", "seconds": round(t, 3), "lines": rows,
                         "text": " ".join(l.text for l in lines)}
        new = max(MIN_STORY_SHOT_S, math.ceil((LEAD_IN_S + t + BREATH_S) * 10) / 10)
        if new != shot.duration_s:
            lengths.append(f"{shot.id}: {shot.duration_s}→{new}s")
            shot.duration_s = new
    for sid in [k for k in meta if k not in {s.id for s in script.shots}]:
        meta.pop(sid)
    project.data["script"] = script.model_dump()
    store.save(project)
    return {"made": made, "lengths": lengths, "total_s": round(sum(s.duration_s for s in script.shots), 1)}


MIN_STORY_SHOT_S = 2.5
