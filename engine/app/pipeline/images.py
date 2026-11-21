"""'images' job: one still image per shot, saved to projects/<id>/assets/images/<shot>.png.

params:
  shot_ids: [..]   only these shots (default: all)
  force: bool      regenerate even if the image exists (bumps the take → new seed)
Resumable: each shot is a step; shots that already have an image are skipped unless forced.
"""
from __future__ import annotations

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
    width, height = size_for(project.aspect)
    out_dir = store.assets_dir(project.id) / "images"
    provider = provider_factory(ctx)
    ctx.cleanups.append(provider.unload)

    made, skipped = [], []
    for i, shot in enumerate(shots):
        out = out_dir / f"{shot.id}.png"
        with ctx.step(f"image-{shot.id}") as todo:
            if todo and (force or not out.exists()):
                meta = project.data.setdefault("images", {}).get(shot.id, {})
                take = meta.get("take", 0) + (1 if force and out.exists() else 0)  # new take = new seed
                prompt = compose(shot, script, project.style)
                seed = shot_seed(project.id, shot.id, take)
                base = i / len(shots)
                provider.generate(
                    prompt, out, width=width, height=height, seed=seed,
                    progress=lambda f, m, base=base, n=i: ctx.progress(
                        None if f is None else base + f / len(shots), f"shot {n + 1}/{len(shots)}: {m}"),
                )
                # Re-read before writing: the user may have edited the project meanwhile.
                project = store.get(project.id)
                project.data.setdefault("images", {})[shot.id] = {
                    "path": f"assets/images/{shot.id}.png", "prompt": prompt, "seed": seed, "take": take,
                    "width": width, "height": height,
                }
                store.save(project)
                made.append(shot.id)
            else:
                skipped.append(shot.id)
        ctx.progress((i + 1) / len(shots), f"shot {i + 1}/{len(shots)} done", force=True)
    return {"made": made, "skipped": skipped, "size": [width, height]}
