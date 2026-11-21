"""'render' job: timeline → per-shot motion clips → video + audio → MP4 per export.

Exports follow the project settings (see images/aspects.py): the main aspect uses its native
images ("cover"); the second aspect (if any) uses its own native images, or the main images
over a blurred background ("blurpad").
params: music (path relative to the project, optional), brand (corner tag text, optional)
Outputs: projects/<id>/exports/<slug>_<16x9|9x16>.mp4
Resumable: clips are cached with a fingerprint of (image, motion, length, aspect, framing);
unchanged clips are reused, changed shots are re-rendered.
"""
from __future__ import annotations

import hashlib
import json
import re

from app.images.aspects import export_plans
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
    pdir = store.assets_dir(project.id).parent
    plans = export_plans(project)
    for plan in plans:
        imgs = d.get(plan.images.key, {})
        missing = [s.id for s in script.shots if s.id not in imgs or not (pdir / imgs[s.id]["path"]).exists()]
        if missing:
            raise ValueError(f"{plan.images.aspect} images missing for: {', '.join(missing)} "
                             "(run images or placeholders first)")
    kids = is_kids(d.get("audience", "children"))
    timelines = {p.aspect: build_timeline(script, pdir, d[p.images.key], d.get("voice", {}), kids=kids)
                 for p in plans}
    tl = timelines[project.aspect]  # same timing for every export; only the images differ
    brand = ctx.params.get("brand", d.get("brand", ""))
    music = ctx.params.get("music") or d.get("music_track")
    slug = re.sub(r"[^a-z0-9]+", "-", script.title.lower()).strip("-") or project.id
    work = pdir / "render"
    n_clips = len(tl.segments) * len(plans)
    # progress budget: clips 60%, audio 10%, video+mux 30%
    done = 0

    for plan in plans:
        aspect, mode = plan.aspect, plan.framing
        cache_file = work / aspect.replace(":", "x") / "clips.json"
        cache = json.loads(cache_file.read_text()) if cache_file.exists() else {}
        for seg in timelines[aspect].segments:
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
    for i, plan in enumerate(plans):
        aspect = plan.aspect
        w, h = OUTPUT_SIZES[aspect]
        adir = work / aspect.replace(":", "x")
        ass = adir / "captions.ass"
        ass.write_text(build_ass(tl, w, h, brand=brand), encoding="utf-8")
        clips = [adir / f"{s.id}.mp4" for s in tl.segments]
        base = 0.7 + 0.3 * i / len(plans)
        video = video_track(tl, clips, ass, adir / "video.mp4", progress=lambda f, m, b=base: ctx.progress(
            None if f is None else b + 0.3 * f / len(plans), f"{aspect} {m}"))
        final = mux(video, audio, pdir / "exports" / f"{slug}_{aspect.replace(':', 'x')}.mp4")
        outputs[aspect] = {"path": str(final.relative_to(pdir)), "seconds": round(duration(final), 2),
                           "framing": plan.framing}

    project = store.get(project.id)
    project.data["exports"] = outputs
    store.save(project)
    return {"duration_s": tl.duration, "exports": outputs}
