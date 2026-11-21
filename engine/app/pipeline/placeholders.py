"""'placeholders' job: grid+label stand-in images for every shot (testing before real images).
Writes the same files/metadata as the images job, marked placeholder: true. Never overwrites
a real image unless params.force."""
from __future__ import annotations

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
    w, h = size_for(project.aspect)
    made = []
    images = project.data.setdefault("images", {})
    for i, shot in enumerate(script.shots):
        out = store.assets_dir(project.id) / "images" / f"{shot.id}.png"
        if out.exists() and not ctx.params.get("force") and not images.get(shot.id, {}).get("placeholder"):
            continue
        make_placeholder(out, shot.id, width=w, height=h, index=i)
        images[shot.id] = {"path": f"assets/images/{shot.id}.png", "placeholder": True, "width": w, "height": h}
        made.append(shot.id)
        ctx.progress((i + 1) / len(script.shots), f"placeholder {shot.id}")
    store.save(project)
    return {"made": made}
