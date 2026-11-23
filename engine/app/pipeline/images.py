"""'images' job: native image(s) per shot.

Always the project's main aspect (assets/images/<shot>.png). If the project's second aspect
uses mode "native", also a second image per shot (assets/images/<9x16>/<shot>.png).

params:
  shot_ids: [..]   only these shots (default: all)
  force: bool      regenerate even if the image exists (bumps the take → new seed)
Resumable: each (shot, aspect) is a step; existing real images are skipped unless forced.
"""
from __future__ import annotations

import re
import shutil

from app.characters.model import CharacterStore
from app.images.aspects import image_sets
from app.images.prompting import compose, shot_seed, size_for
from app.images.story import camera, missing_references, plate_prompt, signature, story_passes
from app.pipeline.story import story_cast
from app.styles import STYLES
from app.providers.comfyui_image import ComfyUIImageProvider
from app.queue import JobContext, register
from app.script.schema import Location, Script
from app.store.projects import ProjectStore


def provider_factory(ctx: JobContext):  # swappable for tests / cloud adapters later
    return ComfyUIImageProvider(ctx.settings.comfyui_url, before_run=ctx.comfyui_ready)


@register("images")
def images_job(ctx: JobContext) -> dict:
    store = ProjectStore(ctx.settings.projects_dir)
    if not ctx.job.project_id:
        raise ValueError("images job needs a project_id")
    project = store.get(ctx.job.project_id)
    if "script" not in project.data:
        raise ValueError("project has no script yet — run the script step first")
    script = Script.model_validate(project.data["script"])
    if project.cast:
        return story_images(ctx, store, project, script)
    wanted = set(ctx.params.get("shot_ids") or [s.id for s in script.shots])
    force = bool(ctx.params.get("force"))
    shots = [s for s in script.shots if s.id in wanted]
    pdir = store.assets_dir(project.id).parent

    # Work list: every (image set, shot) that needs an image.
    todo = []
    for iset in image_sets(project):
        for shot in shots:
            meta = project.data.get(iset.key, {}).get(shot.id, {})
            current = compose(shot, script, project.style, iset.aspect)
            have_real = ((pdir / iset.rel_path(shot.id)).exists() and not meta.get("placeholder")
                         and meta.get("prompt", current) == current)  # edited shot/style → redo
            todo.append((iset, shot, meta, have_real))
    needed = [t for t in todo if force or not t[3]]
    made, skipped = [], [f"{i.aspect}:{s.id}" for i, s, _, real in todo if real and not force]
    if not needed:
        return {"made": [], "skipped": skipped, "sizes": {}}

    ctx.require_model("images")
    provider = provider_factory(ctx)
    ctx.cleanups.append(provider.unload)
    ctx.ensure_service("comfyui")  # starts ComfyUI if it isn't running
    sizes = {}
    for n, (iset, shot, meta, have_real) in enumerate(needed):
        w, h = size_for(iset.aspect)
        sizes[iset.aspect] = [w, h]
        out = pdir / iset.rel_path(shot.id)
        with ctx.step(f"image-{iset.aspect}-{shot.id}") as go:
            if go:
                take = meta.get("take", 0) + (1 if force and have_real else 0)  # new take = new seed
                prompt = compose(shot, script, project.style, iset.aspect)
                seed = shot_seed(project.id, shot.id, take)
                provider.generate(prompt, out, width=w, height=h, seed=seed,
                                  progress=lambda f, m, n=n: ctx.progress(
                                      None if f is None else (n + f) / len(needed),
                                      f"{iset.aspect} shot {shot.id} ({n + 1}/{len(needed)}): {m}"))
                project = store.get(project.id)  # re-read: the user may have edited meanwhile
                project.data.setdefault(iset.key, {})[shot.id] = {
                    "path": iset.rel_path(shot.id), "prompt": prompt, "seed": seed, "take": take,
                    "aspect": iset.aspect, "width": w, "height": h}
                store.save(project)
                made.append(f"{iset.aspect}:{shot.id}")
        ctx.progress((n + 1) / len(needed), f"{n + 1}/{len(needed)} images done", force=True)
    return {"made": made, "skipped": skipped, "sizes": sizes}


# ---- Story Builder -------------------------------------------------------------------
def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "place"


class StoryPainter:
    """Plates + passes for one project, aspect and style; used by the images job and the style preview."""
    def __init__(self, ctx: JobContext, store: ProjectStore, project, script: Script, style: str, aspect: str,
                 size: tuple[int, int], folder: str = "assets", plates_key: str = "plates"):
        self.ctx, self.store, self.pid, self.script, self.style = ctx, store, project.id, script, style
        self.aspect, self.size, self.folder, self.plates_key = aspect, size, folder, plates_key
        self.pdir = store.assets_dir(project.id).parent
        self.chars = CharacterStore(ctx.settings.characters_dir)
        cast = story_cast(ctx.settings, project)
        missing = missing_references(style, cast, script)
        if missing:
            raise ValueError("; ".join(missing) + ". Open Characters to make and lock it, or pick another style.")
        self.cast = {c.name: c for c in cast}

    def plate_id(self, loc: Location) -> str:
        return f"{self.aspect}:{self.style}:{_slug(loc.name)}"

    def _meta(self, pid: str) -> dict:
        return self.store.get(self.pid).data.get(self.plates_key, {}).get(pid, {})

    def location(self, shot) -> Location:
        return self.script.location(shot.location) or Location(name=shot.location or "the place", description=shot.location)

    def plate_sig(self, loc: Location) -> str:
        return f"{plate_prompt(loc, self.style)} #{self._meta(self.plate_id(loc)).get('take', 0)}"

    def plate_ready(self, loc: Location) -> bool:
        m = self._meta(self.plate_id(loc))
        return m.get("prompt") == plate_prompt(loc, self.style) and (self.pdir / m["path"]).exists()

    def plate(self, loc: Location, provider, progress, new_take: bool = False):
        pid, prompt = self.plate_id(loc), plate_prompt(loc, self.style)
        m = self._meta(pid)
        rel = f"{self.folder}/plates/{self.style}/{self.aspect.replace(':', 'x')}-{_slug(loc.name)}.png"
        if self.plate_ready(loc) and not new_take:
            return self.pdir / m["path"]
        take = m.get("take", 0) + (1 if new_take and m else 0)
        seed = shot_seed(self.pid, f"plate-{_slug(loc.name)}", take)
        provider.generate(prompt, self.pdir / rel, width=self.size[0], height=self.size[1], seed=seed, progress=progress)
        p = self.store.get(self.pid)
        p.data.setdefault(self.plates_key, {})[pid] = {"path": rel, "prompt": prompt, "seed": seed, "take": take,
                                                       "location": loc.name, "style": self.style, "aspect": self.aspect}
        self.store.save(p)
        return self.pdir / rel

    def signature(self, shot) -> str:
        return signature(shot, self.script, self.cast, self.chars, self.style, self.plate_sig(self.location(shot)))

    def paint(self, shot, out, take: int, provider, progress) -> None:
        """Camera crop, then one pass per character. Passes are kept, so a retry continues where it stopped."""
        w, h = self.size
        tmp = self.pdir / self.folder / "passes" / self.aspect.replace(":", "x")
        plate = self.plate(self.location(shot), provider, progress)
        cur = camera(plate, shot, tmp / f"{shot.id}-0.png", w, h)
        passes = story_passes(shot, self.cast, self.chars, self.style)
        for k, p in enumerate(passes, 1):
            dst = tmp / f"{shot.id}-{k}.png"
            with self.ctx.step(f"pass-{self.aspect}-{self.style}-{shot.id}-{take}-{k}") as go:
                if go or not dst.exists():
                    provider.generate(p.prompt, dst, width=w, height=h, seed=shot_seed(self.pid, shot.id, take) + k,
                                      references=[cur, *p.refs],
                                      progress=lambda f, m, k=k, n=p.name: progress(f, f"{n or 'picture'} {k}/{len(passes)}: {m}"))
            cur = dst
        out.parent.mkdir(parents=True, exist_ok=True)
        part = out.with_suffix(".part.png")
        shutil.copy2(cur, part)
        part.replace(out)


def story_images(ctx: JobContext, store: ProjectStore, project, script: Script) -> dict:
    """params: shot_ids, force (new take), plate (location name: new take of that place),
    places_only (make the empty places first, to check them before every shot is painted)."""
    wanted = set(ctx.params.get("shot_ids") or [s.id for s in script.shots])
    force, replate = bool(ctx.params.get("force")), ctx.params.get("plate")
    sets = [(i, StoryPainter(ctx, store, project, script, project.style, i.aspect, size_for(i.aspect))) for i in image_sets(project)]
    pdir = store.assets_dir(project.id).parent

    def stale(iset, painter, shot) -> bool:
        m = store.get(project.id).data.get(iset.key, {}).get(shot.id, {})
        return not (pdir / iset.rel_path(shot.id)).exists() or bool(m.get("placeholder")) or m.get("prompt") != painter.signature(shot)

    places_only = bool(ctx.params.get("places_only"))
    work = [] if replate or places_only else [(i, p, s) for i, p in sets for s in script.shots if s.id in wanted and (force or stale(i, p, s))]
    plates = [(p, l) for _, p in sets for l in script.locations
              if (replate and l.name == replate) or (not replate and not p.plate_ready(l) and places_only) or (not replate and not p.plate_ready(l) and any(p.location(s) == l for _, q, s in work if q is p))]
    if not work and not plates:
        return {"made": [], "plates": [], "skipped": sorted(wanted)}
    ctx.require_model("images")
    provider = provider_factory(ctx)
    ctx.cleanups.append(provider.unload)
    ctx.ensure_service("comfyui")
    units = len(plates) + sum(max(1, len(s.cast)) for _, _, s in work)
    done, made = 0, []

    def report(weight, label):
        return lambda f, m: ctx.progress(None if f is None else (done + weight * f) / units, f"{label}: {m}")

    for painter, loc in plates:
        with ctx.step(f"plate-{painter.aspect}-{loc.name}") as go:
            if go or not painter.plate_ready(loc):
                painter.plate(loc, provider, report(1, f"place “{loc.name}”"), new_take=bool(replate))
        done += 1
    for n, (iset, painter, shot) in enumerate(work, 1):
        weight = max(1, len(shot.cast))
        meta = store.get(project.id).data.get(iset.key, {}).get(shot.id, {})
        fresh = not stale(iset, painter, shot)
        with ctx.step(f"image-{iset.aspect}-{shot.id}") as go:
            if go:
                take = meta.get("take", 0) + (1 if force and fresh else 0)
                painter.paint(shot, pdir / iset.rel_path(shot.id), take, provider, report(1, f"{iset.aspect} shot {shot.id} ({n}/{len(work)})"))
                p = store.get(project.id)          # re-read: the user may have edited meanwhile
                w, h = painter.size
                p.data.setdefault(iset.key, {})[shot.id] = {"path": iset.rel_path(shot.id), "prompt": painter.signature(shot), "take": take,
                                                            "seed": shot_seed(project.id, shot.id, take), "aspect": iset.aspect,
                                                            "width": w, "height": h, "passes": max(1, len(shot.cast))}
                store.save(p)
                made.append(f"{iset.aspect}:{shot.id}")
        done += weight
        ctx.progress(done / units, f"{n}/{len(work)} images done", force=True)
    return {"made": made, "plates": [l.name for _, l in plates], "skipped": sorted(wanted - {m.rsplit(':', 1)[-1] for m in made})}


# ---- style preview ------------------------------------------------------------------
PREVIEW_SHOTS = 4


def preview_size(aspect: str) -> tuple[int, int]:
    """~2/3 of the final size (multiples of 16): about half the time per image."""
    w, h = size_for(aspect)
    return (w * 2 // 3) // 16 * 16, (h * 2 // 3) // 16 * 16


def preview_shots(script: Script, n: int = PREVIEW_SHOTS) -> list:
    """n shots spread evenly over the story (first … last)."""
    shots = script.shots
    if len(shots) <= n:
        return shots
    return [shots[round(i * (len(shots) - 1) / (n - 1))] for i in range(n)]


@register("style_preview")
def style_preview_job(ctx: JobContext) -> dict:
    """4 sample images in a style, before generating every shot. Doesn't change the project's
    style; the UI does that when the user picks one. params: style (default: the project's)."""
    store = ProjectStore(ctx.settings.projects_dir)
    project = store.get(ctx.job.project_id or "")
    if "script" not in project.data:
        raise ValueError("project has no script yet — write the script first")
    style = ctx.params.get("style") or project.style
    if style not in STYLES:
        raise ValueError(f"unknown style: {style}")
    script = Script.model_validate(project.data["script"])
    pdir = store.assets_dir(project.id).parent
    w, h = preview_size(project.aspect)
    picks = preview_shots(script)
    if project.cast:
        return _story_preview(ctx, store, project, script, style, picks, (w, h))
    old = project.data.get("style_previews", {}).get(style, {})
    todo = [s for s in picks if old.get(s.id, {}).get("prompt") != compose(s, script, style, project.aspect)
            or not (pdir / old[s.id]["path"]).exists()]
    if todo:
        ctx.require_model("images")
        provider = provider_factory(ctx)
        ctx.cleanups.append(provider.unload)
        ctx.ensure_service("comfyui")
    for n, shot in enumerate(todo):
        rel = f"assets/previews/{style}/{shot.id}.png"
        prompt = compose(shot, script, style, project.aspect)
        with ctx.step(f"preview-{style}-{shot.id}") as go:
            if go:
                provider.generate(prompt, pdir / rel, width=w, height=h, seed=shot_seed(project.id, shot.id),
                                  progress=lambda f, m, n=n: ctx.progress(
                                      None if f is None else (n + f) / len(todo),
                                      f"{STYLES[style]['label']} preview {n + 1}/{len(todo)}: {m}"))
                project = store.get(project.id)
                project.data.setdefault("style_previews", {}).setdefault(style, {})[shot.id] = {
                    "path": rel, "prompt": prompt}
                store.save(project)
    project = store.get(project.id)
    prev = project.data.get("style_previews", {}).get(style, {})
    # keep only the current picks (the script may have changed)
    project.data.setdefault("style_previews", {})[style] = {s.id: prev[s.id] for s in picks if s.id in prev}
    store.save(project)
    return {"style": style, "shots": [s.id for s in picks], "made": [s.id for s in todo], "size": [w, h]}


def _story_preview(ctx: JobContext, store: ProjectStore, project, script: Script, style: str, picks: list, size) -> dict:
    """Style preview of a story: the same plates-and-passes way, smaller, in its own folder."""
    painter = StoryPainter(ctx, store, project, script, style, project.aspect, size, folder=f"assets/previews/{style}",
                           plates_key="preview_plates")
    old = project.data.get("style_previews", {}).get(style, {})
    todo = [s for s in picks if old.get(s.id, {}).get("prompt") != painter.signature(s)
            or not (painter.pdir / old[s.id]["path"]).exists()]
    if todo:
        ctx.require_model("images")
        provider = provider_factory(ctx)
        ctx.cleanups.append(provider.unload)
        ctx.ensure_service("comfyui")
    for n, shot in enumerate(todo):
        rel = f"assets/previews/{style}/{shot.id}.png"
        with ctx.step(f"preview-{style}-{shot.id}") as go:
            if go:
                painter.paint(shot, painter.pdir / rel, 0, provider, lambda f, m, n=n: ctx.progress(
                    None if f is None else (n + f) / len(todo), f"{STYLES[style]['label']} preview {n + 1}/{len(todo)}: {m}"))
                p = store.get(project.id)
                p.data.setdefault("style_previews", {}).setdefault(style, {})[shot.id] = {"path": rel, "prompt": painter.signature(shot)}
                store.save(p)
    p = store.get(project.id)
    prev = p.data.get("style_previews", {}).get(style, {})
    p.data.setdefault("style_previews", {})[style] = {s.id: prev[s.id] for s in picks if s.id in prev}
    store.save(p)
    return {"style": style, "shots": [s.id for s in picks], "made": [s.id for s in todo], "size": list(size)}
