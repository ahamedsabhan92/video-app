"""'render' job: timeline → per-shot motion clips → video + audio → MP4 per export.

Exports follow the project settings (see images/aspects.py): the main aspect uses its native
images ("cover"); the second aspect (if any) uses its own native images, or the main images
over a blurred background ("blurpad").
params: music (path relative to the project, optional), no_music (render without the bed), brand (corner tag text, optional),
        version ("v2": exports are named <slug>_v2_16x9.mp4, next to earlier ones), no_brand (no cards or watermark)
Outputs: projects/<id>/exports/<slug>_<16x9|9x16>.mp4
Resumable: clips are cached with a fingerprint of (image, motion, length, aspect, framing);
unchanged clips are reused, changed shots are re-rendered.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil

from app.creator import brand_file, export_dir, export_metadata, load_profile
from app.images.aspects import export_plans
from app.queue import JobContext, register
from app.render.assemble import audio_track, mux, video_track
from app.render.cards import Card, Watermark, make_card
from app.render.ffmpeg import duration
from app.pipeline.animate import clip_fresh
from app.render.clip import TRIM_S, fit_clip
from app.render.motion import OUTPUT_SIZES, ken_burns
from app.render.subtitles import build_ass
from app.render.timeline import END_S, OPENING_S, build_timeline, set_transitions
from app.script.guards import is_kids
from app.script.schema import Script
from app.store.projects import ProjectStore


def _fingerprint(seg, aspect: str, mode: str) -> str:
    img = seg.image.stat()
    if seg.clip:
        files = [(str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in seg.parts or [seg.clip]]
        return hashlib.sha1(f"{files}:{seg.trim}:{seg.pulse}:{seg.clip_len}:{aspect}:{mode}:clip2".encode()).hexdigest()[:16]
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
    profile = load_profile(ctx.settings.data_dir)
    branded = not ctx.params.get("no_brand")
    logo = brand_file(ctx.settings.brand_dir, profile.card_logo)
    cards: dict[str, Card] = {}
    if branded and profile.opening_card:
        cards["opening"] = Card("opening", OPENING_S, logo, profile.card_background, f"{profile.channel_name} presents", script.title)
    if branded and profile.end_card:
        cards["end"] = Card("end", END_S, logo, profile.card_background, "The End", project.moral.strip())
    mark_logo = brand_file(ctx.settings.brand_dir, profile.watermark_logo) if branded and profile.watermark else None
    mark = Watermark(mark_logo, profile.watermark_corner) if mark_logo else None
    kids = is_kids(d.get("audience", "children"))
    timelines = {p.aspect: build_timeline(script, pdir, d[p.images.key], d.get("voice", {}), kids=kids,
                                           dialogue_captions=project.dialogue_captions,
                                           opening="opening" in cards, end="end" in cards)
                 for p in plans}
    animated: list[str] = []
    if not ctx.params.get("no_clips"):           # clips exist for the main pictures; a native second aspect keeps its stills
        for plan in plans:
            if plan.images.key != "images":
                continue
            for seg in timelines[plan.aspect].segments:
                c = d.get("clips", {}).get(seg.id, {})
                if seg.kind == "shot" and clip_fresh(c, d["images"][seg.id]) and (pdir / c["path"]).exists():
                    seg.clip = pdir / c["path"]
                    seg.parts = [pdir / x for x in c.get("parts", []) if (pdir / x).exists()]
                    if len(seg.parts) != len(c.get("parts", [])):
                        seg.parts = []
                    seg.trim = float(c.get("trim", TRIM_S))
                    seg.pulse = 0 if c.get("decode") else 12      # clips from before the decode fix
                    if plan.aspect == project.aspect:
                        animated.append(seg.id)
            set_transitions(timelines[plan.aspect].segments)
    tl = timelines[project.aspect]  # same timing for every export; only the images differ
    brand = ctx.params.get("brand", d.get("brand", ""))
    music = None if ctx.params.get("no_music") else (ctx.params.get("music") or d.get("music_track"))
    slug = re.sub(r"[^a-z0-9]+", "-", script.title.lower()).strip("-") or project.id
    version = re.sub(r"[^a-z0-9]", "", str(ctx.params.get("version", "")).lower())
    version = f"_{version}" if version else ""
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
            card = cards.get(seg.kind)
            fp = hashlib.sha1(f"{card.key()}:{seg.clip_len}:{aspect}".encode()).hexdigest()[:16] if card else _fingerprint(seg, aspect, mode)
            if cache.get(seg.id) != fp or not out.exists():
                report = lambda f, m, b=done, s=seg: ctx.progress(  # noqa: E731
                    None if f is None else 0.6 * (b + f) / n_clips, f"{aspect} clip {s.id}")
                if card:
                    make_card(card, out, seconds=seg.clip_len, aspect=aspect, fonts=ctx.settings.brand_dir / "fonts", progress=report)
                elif seg.clip:
                    fit_clip(seg.parts or seg.clip, out, seconds=seg.clip_len, aspect=aspect, mode=mode, trim=seg.trim, pulse=seg.pulse, progress=report)
                else:
                    ken_burns(seg.image, out, seconds=seg.clip_len, motion=seg.motion, aspect=aspect, mode=mode, progress=report)
                cache[seg.id] = fp
                cache_file.write_text(json.dumps(cache, indent=1))
            done += 1
            ctx.progress(0.6 * done / n_clips, f"{aspect} clips {done}/{n_clips}")

    audio = audio_track(tl, work / "audio.m4a", music=pdir / music if music else None,
                        progress=lambda f, m: ctx.progress(None if f is None else 0.6 + 0.1 * f, m))
    tags = export_metadata(profile, title=project.title, series=project.series,
                           description=project.description or script.logline or project.idea)
    out_dir = None if ctx.params.get("no_copy") else export_dir(profile, project.title)
    outputs = {}
    for i, plan in enumerate(plans):
        aspect = plan.aspect
        w, h = OUTPUT_SIZES[aspect]
        adir = work / aspect.replace(":", "x")
        ass = adir / "captions.ass"
        placed = mark.place(w, h, plan.framing == "blurpad") if mark else None
        beside = placed and h <= w and "bottom" in profile.watermark_corner        # captions and logo share the bottom
        ass.write_text(build_ass(tl, w, h, brand=brand, keep_clear=w - placed[1] + round(w * 0.01) if beside and "right" in profile.watermark_corner
                                 else placed[1] + placed[0] + round(w * 0.01) if beside else 0), encoding="utf-8")
        clips = [adir / f"{s.id}.mp4" for s in tl.segments]
        base = 0.7 + 0.3 * i / len(plans)
        video = video_track(tl, clips, ass, adir / "video.mp4", progress=lambda f, m, b=base: ctx.progress(
            None if f is None else b + 0.3 * f / len(plans), f"{aspect} {m}"), watermark=(mark.logo, *placed, mark.opacity) if placed else None)
        final = mux(video, audio, pdir / "exports" / f"{slug}{version}_{aspect.replace(':', 'x')}.mp4", metadata=tags)
        copy = None
        if out_dir:
            out_dir.mkdir(parents=True, exist_ok=True)
            copy = out_dir / final.name
            shutil.copy2(final, copy)
        outputs[aspect] = {"path": str(final.relative_to(pdir)), "seconds": round(duration(final), 2),
                           "copy": str(copy) if copy else None,
                           "framing": plan.framing}

    project = store.get(project.id)
    project.data["exports"] = outputs
    store.save(project)
    return {"duration_s": tl.duration, "exports": outputs, "animated": animated}
