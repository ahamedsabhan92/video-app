"""'music' job: one background track per project from the script's music block
(assets/music/bed.flac → data.music_track). The render step loops/trims it, fades it and
ducks it under the narration.

params: force (new take), seed
"""
from __future__ import annotations

from app.render.timeline import INTRO_S
from app.providers.comfyui_music import MAX_SECONDS, ComfyUIMusicProvider, music_prompt
from app.queue import JobContext, register
from app.render.ffmpeg import duration
from app.script.guards import is_kids
from app.script.schema import Script
from app.store.projects import ProjectStore
from app.images.prompting import shot_seed

TAIL_S = 3.0  # a little extra so the fade-out isn't cut short


def provider_factory(ctx: JobContext) -> ComfyUIMusicProvider:
    return ComfyUIMusicProvider(ctx.settings.comfyui_url)


@register("music")
def music_job(ctx: JobContext) -> dict:
    store = ProjectStore(ctx.settings.projects_dir)
    project = store.get(ctx.job.project_id or "")
    if "script" not in project.data:
        raise ValueError("project has no script yet")
    script = Script.model_validate(project.data["script"])
    kids = is_kids(project.data.get("audience", "children 4-8"))
    m = script.music
    prompt = music_prompt(m.mood, m.tempo, m.instruments, kids=kids)
    seconds = min(MAX_SECONDS, script.duration_s + INTRO_S + TAIL_S)
    take = int(ctx.params.get("take", project.data.get("music", {}).get("take", 0)))
    if ctx.params.get("force"):
        take += 1
    out = store.assets_dir(project.id) / "music" / "bed.flac"
    old = project.data.get("music", {})
    fresh = out.exists() and old.get("prompt") == prompt and old.get("take") == take \
        and old.get("requested", 0) >= seconds - 1
    if not fresh:
        ctx.require_model("music")
        ctx.ensure_service("comfyui")
        provider = provider_factory(ctx)
        ctx.cleanups.append(provider.unload)
        ctx.progress(0.02, "composing music")
        provider.compose(prompt, out, seconds=seconds, seed=shot_seed(project.id, "music", take),
                         progress=lambda f, msg: ctx.progress(f, f"music: {msg}"))
    project = store.get(project.id)
    project.data["music"] = {"path": "assets/music/bed.flac", "prompt": prompt, "take": take,
                             "seconds": round(duration(out), 2), "requested": seconds, "mood": m.mood}
    project.data["music_track"] = "assets/music/bed.flac"
    store.save(project)
    return {"made": not fresh, **project.data["music"]}
