"""'render' job: timeline → per-shot motion clips → video + audio → MP4 per aspect.

params:
  aspects: ["16:9", "9:16"]  (default both; the project's own aspect is framed "cover",
                               the other "blurpad")
  music: path relative to the project (optional; MusicGen step will fill this later)
  brand: small corner tag text (optional)
Outputs: projects/<id>/exports/<slug>_<16x9|9x16>.mp4
Resumable: clips are cached with a fingerprint of (image, motion, length, aspect); unchanged
clips are reused, changed shots are re-rendered.
"""
from __future__ import annotations

import hashlib
import json
import re

from app.queue import JobContext, register
from app.render.assemble import audio_track, mux, video_track
from app.render.ffmpeg import duration
from app.render.motion import OUTPUT_SIZES, ken_burns
from app.render.subtitles import build_ass
from app.render.timeline import build_timeline
from app.script.guards import is_kids
from app.script.schema import Script
from app.store.projects import ProjectStore


def _fingerprint(seg, aspect: str, mode: str) -> str:
    img = seg.image.stat()
    key = f"{seg.image}:{img.st_mtime_ns}:{img.st_size}:{seg.motion}:{seg.clip_len}:{aspect}:{mode}:v1"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


@register("render")
def render_job(ctx: JobContext) -> dict:
    store = ProjectStore(ctx.settings.projects_dir)
    project = store.get(ctx.job.project_id or "")
    d = project.data
    if "script" not in d:
        raise ValueError("project has no script yet")
    script = Script.model_validate(d["script"])
    images = d.get("images", {})
    missing = [s.id for s in script.shots if s.id not in images]
    if missing:
        raise ValueError(f"shots without images: {', '.join(missing)} (run images or placeholders first)")
    pdir = store.assets_dir(project.id).parent
    kids = is_kids(d.get("audience", "children"))
    tl = build_timeline(script, pdir, images, d.get("voice", {}), kids=kids)
    aspects = ctx.params.get("aspects") or ["16:9", "9:16"]
    brand = ctx.params.get("brand", d.get("brand", ""))
    music = ctx.params.get("music") or d.get("music_track")
    slug = re.sub(r"[^a-z0-9]+", "-", script.title.lower()).strip("-") or project.id
    work = pdir / "render"
    n_clips = len(tl.segments) * len(aspects)
    # progress budget: clips 60%, audio 10%, video+mux 30%
    done = 0

    for aspect in aspects:
        mode = "cover" if aspect == project.aspect else "blurpad"
        cache_file = work / aspect.replace(":", "x") / "clips.json"
        cache = json.loads(cache_file.read_text()) if cache_file.exists() else {}
        for seg in tl.segments:
            out = cache_file.parent / f"{seg.id}.mp4"
            fp = _fingerprint(seg, aspect, mode)
            if cache.get(seg.id) != fp or not out.exists():
                ken_burns(seg.image, out, seconds=seg.clip_len, motion=seg.motion, aspect=aspect, mode=mode,
                          progress=lambda f, m, b=done: ctx.progress(
                              None if f is None else 0.6 * (b + f) / n_clips, f"{aspect} clip {seg.id}"))
                cache[seg.id] = fp
                cache_file.write_text(json.dumps(cache, indent=1))
            done += 1
            ctx.progress(0.6 * done / n_clips, f"{aspect} clips {done}/{n_clips}")

    audio = audio_track(tl, work / "audio.m4a", music=pdir / music if music else None,
                        progress=lambda f, m: ctx.progress(None if f is None else 0.6 + 0.1 * f, m))
    outputs = {}
    for i, aspect in enumerate(aspects):
        w, h = OUTPUT_SIZES[aspect]
        adir = work / aspect.replace(":", "x")
        ass = adir / "captions.ass"
        ass.write_text(build_ass(tl, w, h, brand=brand), encoding="utf-8")
        clips = [adir / f"{s.id}.mp4" for s in tl.segments]
        base = 0.7 + 0.3 * i / len(aspects)
        video = video_track(tl, clips, ass, adir / "video.mp4", progress=lambda f, m, b=base: ctx.progress(
            None if f is None else b + 0.3 * f / len(aspects), f"{aspect} {m}"))
        final = mux(video, audio, pdir / "exports" / f"{slug}_{aspect.replace(':', 'x')}.mp4")
        outputs[aspect] = {"path": str(final.relative_to(pdir)), "seconds": round(duration(final), 2)}

    project = store.get(project.id)
    project.data["exports"] = outputs
    store.save(project)
    return {"duration_s": tl.duration, "exports": outputs}
