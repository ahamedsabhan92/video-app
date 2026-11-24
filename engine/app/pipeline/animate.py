"""'animate' job: a moving clip per shot from its picture (Wan 2.2 TI2V-5B).

params: shot_ids, force (new take = new seed), level (start level), max_minutes (default 20), max_pieces
- One step per shot (resumable); a failed or too slow clip is tried once more one level lower, then the job goes on
  with the other shots and reports what failed.
- A clip that went over the memory guard's limits or the time limit makes the NEXT clips start one level lower.
- Long shots get several pieces: each continues from the last frame of the one before.
data.clips[shot] = path, settings, seconds it took, peak memory, prompt, picture it was made from.
"""
from __future__ import annotations

import math
import time

from app.pipeline.story import story_cast
from app.providers.comfyui_video import ComfyUIVideoProvider, VideoSettings
from app.queue import JobContext, register
from app.queue.worker import JobCancelled
from app.images.prompting import shot_seed
from app.render.clip import MAX_SLOW, join, last_frame
from app.render.ffmpeg import duration
from app.render.timeline import XFADE_S
from app.script.schema import Script
from app.store.projects import ProjectStore
from app.story.motion import motion_prompt

# from good to safe; every size a multiple of 32, 16:9 (9:16 projects are turned)
LEVELS = [VideoSettings(1280, 704, 49), VideoSettings(960, 544, 65), VideoSettings(832, 480, 81), VideoSettings(832, 480, 49),
          VideoSettings(704, 384, 49), VideoSettings(640, 352, 33)]
DEFAULT_LEVEL = 2
MAX_PIECES = 3
PEAK_LIMIT_GB = 10.0        # the guard's limit, for drawing + decoding. Loading the text encoder touches ~12 GB for a few
                            # seconds whatever the settings are, so it is recorded (load_peak_gb) but doesn't lower them.


def provider_factory(ctx: JobContext):
    return ComfyUIVideoProvider(ctx.settings.comfyui_url, before_run=lambda: ctx.comfyui_ready("video"))


def settings_for(level: int, aspect: str) -> VideoSettings:
    s = LEVELS[min(max(level, 0), len(LEVELS) - 1)]
    return VideoSettings(s.height, s.width, s.frames, s.steps, s.cfg, s.shift) if aspect in ("9:16", "4:5") else s


def pieces_for(seconds: float, s: VideoSettings) -> int:
    return max(1, min(MAX_PIECES, math.ceil(seconds / (s.seconds * MAX_SLOW) - 0.05)))


def picture_id(meta: dict) -> str:
    return f"{meta.get('take', 0)}:{meta.get('seed')}:{hash_text(meta.get('prompt', ''))}"


def hash_text(t: str) -> str:
    import hashlib
    return hashlib.sha1(t.encode()).hexdigest()[:12]


def clip_fresh(clip: dict, image_meta: dict) -> bool:
    """Made from the picture the shot has now (a new take or an edited shot needs a new clip)."""
    return bool(clip.get("path")) and clip.get("picture") == picture_id(image_meta)


@register("animate")
def animate_job(ctx: JobContext) -> dict:
    store = ProjectStore(ctx.settings.projects_dir)
    project = store.get(ctx.job.project_id or "")
    if "script" not in project.data:
        raise ValueError("project has no shots yet")
    script = Script.model_validate(project.data["script"])
    cast = {c.name: c for c in story_cast(ctx.settings, project)}
    pdir = store.assets_dir(project.id).parent
    wanted = set(ctx.params.get("shot_ids") or [s.id for s in script.shots])
    force, limit_s = bool(ctx.params.get("force")), float(ctx.params.get("max_minutes", 20)) * 60
    level = int(ctx.params.get("level", project.data.get("video_level", DEFAULT_LEVEL)))
    images, clips = project.data.get("images", {}), project.data.get("clips", {})
    missing = [s.id for s in script.shots if s.id in wanted and s.id not in images]
    if missing:
        raise ValueError(f"make the images first: {', '.join(missing)}")
    todo = []
    for s in script.shots:
        if s.id not in wanted:
            continue
        c = clips.get(s.id, {})
        same = clip_fresh(c, images[s.id]) and c.get("prompt") == motion_prompt(s, cast, project.style) and (pdir / c["path"]).exists()
        if force or not same:
            todo.append(s)
    if not todo:
        return {"made": [], "failed": {}}
    ctx.require_model("video")
    provider = provider_factory(ctx)
    ctx.cleanups.append(provider.unload)
    ctx.ensure_service("comfyui", "video")
    made, failed = [], {}
    for n, shot in enumerate(todo):
        with ctx.step(f"clip-{shot.id}") as go:
            if go:
                err = None
                for attempt in range(2):                       # second try: one level safer
                    try:
                        row = _make(ctx, provider, project, shot, cast, pdir, images[shot.id], min(level + attempt, len(LEVELS) - 1),
                                    attempt, limit_s, n, len(todo), clips.get(shot.id, {}).get("take", -1) + 1)
                        err = None
                        break
                    except JobCancelled:
                        raise
                    except Exception as e:  # noqa: BLE001 — one bad shot must not stop the night
                        err = f"{type(e).__name__}: {e}"
                        ctx.memory_event(f"clip {shot.id} failed at level {level + attempt}: {err[:200]}")
                        if attempt == 0:
                            provider.unload()
                            ctx.ensure_service("comfyui", "video")
                if err:
                    failed[shot.id] = err[:300]
                    p = store.get(project.id)
                    p.data.setdefault("clip_errors", {})[shot.id] = err[:500]
                    store.save(p)
                else:
                    p = store.get(project.id)
                    p.data.setdefault("clips", {})[shot.id] = row
                    p.data.get("clip_errors", {}).pop(shot.id, None)
                    over = row["over"]
                    if over and level < len(LEVELS) - 1:
                        level = max(level, row["level"]) + 1     # the next clips start safer
                        ctx.memory_event(f"clip {shot.id}: {over}; next clips at level {level} ({LEVELS[min(level, len(LEVELS) - 1)].label()})")
                    else:
                        level = max(level, row["level"])
                    p.data["video_level"] = min(level, len(LEVELS) - 1)
                    store.save(p)
                    made.append(shot.id)
        ctx.progress((n + 1) / len(todo), f"{n + 1}/{len(todo)} clips", force=True)
    return {"made": made, "failed": failed, "level": min(level, len(LEVELS) - 1)}


def _make(ctx: JobContext, provider, project, shot, cast, pdir, image_meta, level: int, attempt: int, limit_s: float, n: int,
          total: int, take: int = 0) -> dict:
    s = settings_for(level, project.aspect)
    need = shot.duration_s + XFADE_S
    pieces = min(pieces_for(need, s), int(ctx.params.get("max_pieces", MAX_PIECES)))   # each further piece drifts
    prompt = motion_prompt(shot, cast, project.style)
    work = pdir / "assets" / "clips"
    start = pdir / image_meta["path"]
    parts, times, peak, load_peak, swap = [], [], 0.0, 0.0, 0.0
    for k in range(pieces):
        part = work / "parts" / f"{shot.id}-{k + 1}.mp4"
        seed = shot_seed(project.id, f"clip-{shot.id}", 10 * take + attempt) + k
        t0 = time.monotonic()
        loaded = []
        with ctx.watch_memory() as w:
            def report(f, m, k=k):
                if not loaded and m.startswith("sampling"):      # from here on the settings decide
                    loaded.append(w.get("comfy_gb") or 0)
                    w["comfy_gb"] = 0
                ctx.progress(None if f is None else (n + (k + f) / pieces) / total,
                             f"clip {shot.id} ({n + 1}/{total}) piece {k + 1}/{pieces}: {m}")
            provider.animate(start, part, motion_prompt=prompt, seconds=s.seconds, seed=seed, settings=s, timeout_s=limit_s,
                             progress=report)
        times.append(round(time.monotonic() - t0))
        peak, swap = max(peak, w.get("comfy_gb") or 0), max(swap, w.get("swap_gb") or 0)
        load_peak = max(load_peak, loaded[0] if loaded else 0)
        parts.append(part)
        if k + 1 < pieces:
            start = last_frame(part, work / "parts" / f"{shot.id}-{k + 1}-last.png")
    out = work / f"{shot.id}.mp4"
    join(parts, out)
    over = []
    if max(times) > limit_s:
        over.append(f"took {max(times) / 60:.0f} min")
    if peak > PEAK_LIMIT_GB:
        over.append(f"peak {peak:.1f} GB")
    if swap > ctx.swap_limit_gb:
        over.append(f"swap {swap:.1f} GB")
    return {"path": f"assets/clips/{shot.id}.mp4", "seconds": round(duration(out), 2), "need": round(need, 2), "pieces": pieces,
            "level": level, "settings": s.dict(), "took_s": sum(times), "piece_s": times, "peak_gb": round(peak, 1), "load_peak_gb": round(load_peak, 1), "swap_gb": round(swap, 1),
            "prompt": prompt, "picture": picture_id(image_meta), "seed": shot_seed(project.id, f"clip-{shot.id}", 10 * take + attempt), "take": take,
            "attempt": attempt + 1, "over": ", ".join(over), "model": provider.name}
