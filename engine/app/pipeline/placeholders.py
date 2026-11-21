"""'placeholders' job: grid+label stand-in images for every image the project needs (main
aspect, plus the second aspect when it's "native"). Same files/metadata as the images job,
marked placeholder: true. Never overwrites a real image unless params.force."""
from __future__ import annotations

from app.images.aspects import image_sets, tag
from app.images.prompting import size_for
from app.queue import JobContext, register
from app.render.placeholder import make_placeholder
from app.script.schema import Script
from app.store.projects import ProjectStore


@register("placeholders")
def placeholders_job(ctx: JobContext) -> dict:
    store = ProjectStore(ctx.settings.projects_dir)
    project = store.get(ctx.job.project_id or "")
    script = Script.model_validate(project.data["script"])
    pdir = store.assets_dir(project.id).parent
    made = []
    for iset in image_sets(project):
        w, h = size_for(iset.aspect)
        meta = project.data.setdefault(iset.key, {})
        for i, shot in enumerate(script.shots):
            out = pdir / iset.rel_path(shot.id)
            if out.exists() and not ctx.params.get("force") and not meta.get(shot.id, {}).get("placeholder"):
                continue
            make_placeholder(out, f"{shot.id} {tag(iset.aspect)}", width=w, height=h, index=i)
            meta[shot.id] = {"path": iset.rel_path(shot.id), "placeholder": True, "aspect": iset.aspect,
                             "width": w, "height": h}
            made.append(f"{iset.aspect}:{shot.id}")
        ctx.progress(None, f"placeholders {iset.aspect}")
    store.save(project)
    return {"made": made}
