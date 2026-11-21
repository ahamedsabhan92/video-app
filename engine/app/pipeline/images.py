"""'images' job: native image(s) per shot.

Always the project's main aspect (assets/images/<shot>.png). If the project's second aspect
uses mode "native", also a second image per shot (assets/images/<9x16>/<shot>.png).

params:
  shot_ids: [..]   only these shots (default: all)
  force: bool      regenerate even if the image exists (bumps the take → new seed)
Resumable: each (shot, aspect) is a step; existing real images are skipped unless forced.
"""
from __future__ import annotations

from app.images.aspects import image_sets
from app.images.prompting import compose, shot_seed, size_for
from app.providers.comfyui_image import ComfyUIImageProvider
from app.queue import JobContext, register
from app.script.schema import Script
from app.store.projects import ProjectStore


def provider_factory(ctx: JobContext):  # swappable for tests / cloud adapters later
    return ComfyUIImageProvider(ctx.settings.comfyui_url)


@register("images")
def images_job(ctx: JobContext) -> dict:
    store = ProjectStore(ctx.settings.projects_dir)
    if not ctx.job.project_id:
        raise ValueError("images job needs a project_id")
    project = store.get(ctx.job.project_id)
    if "script" not in project.data:
        raise ValueError("project has no script yet — run the script step first")
    script = Script.model_validate(project.data["script"])
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
