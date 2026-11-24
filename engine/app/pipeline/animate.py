"""'animate' job: a moving clip per shot from its picture (Wan 2.2 TI2V-5B).

params: shot_ids, force (new take = new seed), level (start level), max_minutes (default 20), max_pieces
- One step per shot (resumable); a failed or too slow clip is tried once more one level lower, then the job goes on
  with the other shots and reports what failed.
- A clip that went over the memory guard's limits or the time limit makes the NEXT clips start one level lower.
- All prompts are read first, in a run of their own (the text encoder never shares memory with the video model).
- Long shots get a second piece: a closer view of the SAME picture, as a camera cut (continuing from the last frame
  carried its mistakes on and added more).
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
from app.images.story import FOCUS_XY
from app.render.clip import BACK_S, MAX_SLOW, TRIM_S, crop_picture, join, usable
from app.render.ffmpeg import duration
from app.render.timeline import CUT_S
from app.script.schema import Script
from app.store.projects import ProjectStore
from app.story.motion import motion_prompt

# from good to safe; every size a multiple of 32, 16:9 (9:16 projects are turned)
LEVELS = [VideoSettings(1280, 704, 49), VideoSettings(960, 544, 65), VideoSettings(832, 480, 81), VideoSettings(832, 480, 49),
          VideoSettings(704, 384, 49), VideoSettings(640, 352, 33)]
DEFAULT_LEVEL = 2
MAX_PIECES = 2
CLOSER = (1.0, 0.64)        # how much of the picture piece 1 and 2 show
PEAK_LIMIT_GB = 10.0        # the guard's limit, for the whole clip


def provider_factory(ctx: JobContext):
    return ComfyUIVideoProvider(ctx.settings.comfyui_url, before_run=lambda: ctx.comfyui_ready("video"))


def settings_for(level: int, aspect: str) -> VideoSettings:
    s = LEVELS[min(max(level, 0), len(LEVELS) - 1)]
    return VideoSettings(s.height, s.width, s.frames, s.steps, s.cfg, s.shift) if aspect in ("9:16", "4:5") else s


def pieces_for(seconds: float, s: VideoSettings) -> int:
    one = usable(s.seconds, TRIM_S) * MAX_SLOW + BACK_S
    return max(1, min(MAX_PIECES, math.ceil(seconds / one)))


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
    encode = _read_prompts(ctx, provider, [motion_prompt(s, cast, project.style) for s in todo])
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
    return {"made": made, "failed": failed, "level": min(level, len(LEVELS) - 1), "prompts": encode}


def _read_prompts(ctx: JobContext, provider, prompts: list[str]) -> dict:
    t0 = time.monotonic()
    with ctx.watch_memory() as w:
        provider.prepare(prompts, progress=lambda f, m: ctx.progress(None, f"reading {len(set(prompts))} prompts: {m}"))
    ctx.restart_service("comfyui", "video")      # so nothing of the encoder is left when the video model loads
    row = {"count": len(set(prompts)), "took_s": round(time.monotonic() - t0), "peak_gb": round(w.get("comfy_gb") or 0, 1)}
    ctx.memory_event(f"read {row['count']} prompts in {row['took_s']} s, peak {row['peak_gb']} GB")
    return row


def _make(ctx: JobContext, provider, project, shot, cast, pdir, image_meta, level: int, attempt: int, limit_s: float, n: int,
          total: int, take: int = 0) -> dict:
    s = settings_for(level, project.aspect)
    need = shot.duration_s + CUT_S
    pieces = min(pieces_for(need, s), int(ctx.params.get("max_pieces", MAX_PIECES)))
    prompt = motion_prompt(shot, cast, project.style)
    work = pdir / "assets" / "clips"
    picture = pdir / image_meta["path"]
    parts, times, peak, swap = [], [], 0.0, 0.0
    for k in range(pieces):
        part = work / "parts" / f"{shot.id}-{k + 1}.mp4"
        start = picture if k == 0 else crop_picture(picture, work / "parts" / f"{shot.id}-{k + 1}-start.png", CLOSER[k],
                                                    *FOCUS_XY.get(shot.focus, (.5, .5)))
        seed = shot_seed(project.id, f"clip-{shot.id}", 10 * take + attempt) + k
        t0 = time.monotonic()
        with ctx.watch_memory() as w:
            provider.animate(start, part, motion_prompt=prompt, seconds=s.seconds, seed=seed, settings=s, timeout_s=limit_s,
                             progress=lambda f, m, k=k: ctx.progress(None if f is None else (n + (k + f) / pieces) / total,
                                                                     f"clip {shot.id} ({n + 1}/{total}) piece {k + 1}/{pieces}: {m}"))
        times.append(round(time.monotonic() - t0))
        peak, swap = max(peak, w.get("comfy_gb") or 0), max(swap, w.get("swap_gb") or 0)
        parts.append(part)
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
            "parts": [str(p.relative_to(pdir)) for p in parts], "trim": TRIM_S, "decode": 2,
            "level": level, "settings": s.dict(), "took_s": sum(times), "piece_s": times, "peak_gb": round(peak, 1), "swap_gb": round(swap, 1),
            "prompt": prompt, "picture": picture_id(image_meta), "seed": shot_seed(project.id, f"clip-{shot.id}", 10 * take + attempt), "take": take,
            "attempt": attempt + 1, "over": ", ".join(over), "model": provider.name}
