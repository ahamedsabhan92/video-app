"""'animate' job: a moving clip per shot from its picture (Wan 2.2 TI2V-5B).

params: shot_ids, force (new take = new seed), level (start level), max_minutes (default 20), max_pieces,
        only_piece (1 or 2: make just that piece again and keep the other)
- One step per shot (resumable); a failed or too slow clip is tried once more one level lower, then the job goes on
  with the other shots and reports what failed.
- A clip that went over the memory guard's limits or the time limit makes the NEXT clips start one level lower.
- All prompts are read first, in a run of their own (the text encoder never shares memory with the video model).
- Clips play at their own speed, forwards. A shot longer than one clip gets more pieces: closer views of the SAME
  picture, as camera cuts (continuing from the last frame carried its mistakes on and added more); a little that is
  then still missing is a held last frame. Each piece is only as long as it has to be (short clips drift less).
data.clips[shot] = path, settings, seconds it took, peak memory, prompt, picture it was made from.
"""
from __future__ import annotations

import math
import time
from dataclasses import replace

from app.pipeline.story import story_cast
from app.providers.comfyui_video import ComfyUIVideoProvider, VideoSettings
from app.queue import JobContext, register
from app.queue.worker import JobCancelled
from app.images.prompting import shot_seed
from app.render.clip import HOLD_S, TRIM_S, crop_picture, join, usable
from app.render.ffmpeg import duration
from app.render.motion import CLIP_FPS
from app.render.timeline import CUT_FRAMES, END_FADE_S, LEAD_IN_S
from app.script.schema import Script
from app.store.projects import ProjectStore
from app.story.motion import Piece, motion_prompt, plan_pieces

# from good to safe; every size a multiple of 32, 16:9 (9:16 projects are turned)
LEVELS = [VideoSettings(1280, 704, 49), VideoSettings(960, 544, 65), VideoSettings(832, 480, 81), VideoSettings(832, 480, 49),
          VideoSettings(704, 384, 49), VideoSettings(640, 352, 33)]
DEFAULT_LEVEL = 2
MAX_PIECES = 4
MIN_FRAMES = 33
PEAK_LIMIT_GB = 10.0        # the guard's limit, for the whole clip


def provider_factory(ctx: JobContext):
    return ComfyUIVideoProvider(ctx.settings.comfyui_url, before_run=lambda: ctx.comfyui_ready("video"))


def settings_for(level: int, aspect: str) -> VideoSettings:
    s = LEVELS[min(max(level, 0), len(LEVELS) - 1)]
    return VideoSettings(s.height, s.width, s.frames, s.steps, s.cfg, s.shift) if aspect in ("9:16", "4:5") else s


def need_s(shot, last: bool) -> float:
    """How long the shot's clip has to be: on screen + the cut; the last one also runs under the fade to the end card."""
    return shot.duration_s + (END_FADE_S if last else CUT_FRAMES / CLIP_FPS)


def pieces_of(shot, voice: dict, s: VideoSettings, last: bool = False, most: int = MAX_PIECES) -> list[Piece]:
    lines = [(v["speaker"], v["text"], LEAD_IN_S + v["start"], v["seconds"]) for v in voice.get("lines") or []]
    hold = HOLD_S + (END_FADE_S / 2 if last else 0)          # a still frame inside the fade isn't seen
    return plan_pieces(shot, need_s(shot, last), usable(s.seconds, TRIM_S), hold, lines, most)


def frames_for(seconds: float, s: VideoSettings) -> int:
    """Frames (4k+1) for a piece of this length plus its drifting end."""
    n = math.ceil((seconds + TRIM_S) * CLIP_FPS)
    return max(MIN_FRAMES, min(s.frames, n + (1 - n) % 4))


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
    images, clips, voice = project.data.get("images", {}), project.data.get("clips", {}), project.data.get("voice", {})
    end = script.shots[-1].id
    most = int(ctx.params.get("max_pieces", MAX_PIECES))

    def plan(s, lv: int = level) -> list[Piece]:
        return pieces_of(s, voice.get(s.id, {}), settings_for(lv, project.aspect), s.id == end, most)

    def prompts(s, lv: int = level) -> list[str]:
        return [motion_prompt(p.shot, cast, project.style, s) for p in plan(s, lv)]

    missing = [s.id for s in script.shots if s.id in wanted and s.id not in images]
    if missing:
        raise ValueError(f"make the images first: {', '.join(missing)}")
    todo = []
    for s in script.shots:
        if s.id not in wanted:
            continue
        c = clips.get(s.id, {})
        same = clip_fresh(c, images[s.id]) and c.get("prompts") == prompts(s) and (pdir / c["path"]).exists()
        if force or not same:
            todo.append(s)
    if not todo:
        return {"made": [], "failed": {}}
    ctx.require_model("video")
    provider = provider_factory(ctx)
    ctx.cleanups.append(provider.unload)
    ctx.ensure_service("comfyui", "video")
    encode = _read_prompts(ctx, provider, [p for s in todo for lv in {level, min(level + 1, len(LEVELS) - 1)} for p in prompts(s, lv)])
    made, failed = [], {}
    for n, shot in enumerate(todo):
        with ctx.step(f"clip-{shot.id}") as go:
            if go:
                err = None
                for attempt in range(2):                       # second try: one level safer
                    try:
                        lv = min(level + attempt, len(LEVELS) - 1)
                        row = _make(ctx, provider, project, shot, plan(shot, lv), prompts(shot, lv), pdir, images[shot.id], lv, attempt, limit_s, n, len(todo), clips.get(shot.id, {}).get("take", -1) + 1)
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


def _make(ctx: JobContext, provider, project, shot, plan: list[Piece], prompts: list[str], pdir, image_meta, level: int, attempt: int,
          limit_s: float, n: int, total: int, take: int = 0) -> dict:
    base = settings_for(level, project.aspect)
    pieces = len(plan)
    only = int(ctx.params.get("only_piece") or 0)
    work = pdir / "assets" / "clips"
    picture = pdir / image_meta["path"]
    parts, times, frames, peak, swap = [], [], [], 0.0, 0.0
    for k, piece in enumerate(plan):
        s = replace(base, frames=frames_for(piece.seconds, base))
        part = work / "parts" / f"{shot.id}-{k + 1}.mp4"
        if only and k + 1 != only and part.exists():
            times.append(0)
            parts.append(part)
            frames.append(round(duration(part) * CLIP_FPS))
            continue
        start = picture if k == 0 else crop_picture(picture, work / "parts" / f"{shot.id}-{k + 1}-start.png", *piece.crop)
        seed = shot_seed(project.id, f"clip-{shot.id}", 10 * take + attempt) + k
        if n or k:       # one ComfyUI process over several clips piled up swap (3.5 → 11 GB), whatever the clip settings
            ctx.restart_service("comfyui", "video")
        t0 = time.monotonic()
        with ctx.watch_memory() as w:
            provider.animate(start, part, motion_prompt=prompts[k], seconds=s.seconds, seed=seed, settings=s, timeout_s=limit_s,
                             progress=lambda f, m, k=k: ctx.progress(None if f is None else (n + (k + f) / pieces) / total,
                                                                     f"clip {shot.id} ({n + 1}/{total}) piece {k + 1}/{pieces}: {m}"))
        times.append(round(time.monotonic() - t0))
        peak, swap = max(peak, w.get("comfy_gb") or 0), max(swap, w.get("swap_gb") or 0)
        parts.append(part)
        frames.append(s.frames)
    out = work / f"{shot.id}.mp4"
    join(parts, out)
    over = []
    if max(times) > limit_s:
        over.append(f"took {max(times) / 60:.0f} min")
    if peak > PEAK_LIMIT_GB:
        over.append(f"peak {peak:.1f} GB")
    if swap > ctx.swap_limit_gb:
        over.append(f"swap {swap:.1f} GB")
    return {"path": f"assets/clips/{shot.id}.mp4", "seconds": round(duration(out), 2), "need": round(sum(p.seconds for p in plan), 2), "pieces": pieces,
            "cuts": [p.start for p in plan[1:]], "frames": frames,
            "hold_s": round(max(0.0, *(p.seconds - usable(f / CLIP_FPS, TRIM_S) for p, f in zip(plan, frames))), 2),
            "parts": [str(p.relative_to(pdir)) for p in parts], "trim": TRIM_S, "decode": 2,
            "level": level, "settings": base.dict(), "took_s": sum(times), "piece_s": times, "peak_gb": round(peak, 1), "swap_gb": round(swap, 1),
            "prompt": prompts[0], "prompts": prompts, "picture": picture_id(image_meta), "seed": shot_seed(project.id, f"clip-{shot.id}", 10 * take + attempt), "take": take,
            "attempt": attempt + 1, "over": ", ".join(over), "model": provider.name}
