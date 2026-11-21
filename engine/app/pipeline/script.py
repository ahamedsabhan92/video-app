"""'script' job: idea → script JSON saved into the project.

params: idea, seconds, audience, language, aspect, characters (list), think (bool), review (bool)
"""
from __future__ import annotations

from app.providers.ollama_script import OllamaScriptProvider
from app.queue import JobContext, register
from app.script.guards import Brief
from app.script.writer import write_script
from app.store.projects import ProjectStore

DEFAULT_THINK = False


def provider_factory(ctx: JobContext):  # swappable for tests / cloud adapters later
    return OllamaScriptProvider(ctx.settings.ollama_url, think=bool(ctx.params.get("think", DEFAULT_THINK)))


@register("script")
def script_job(ctx: JobContext) -> dict:
    store = ProjectStore(ctx.settings.projects_dir)
    if not ctx.job.project_id:
        raise ValueError("script job needs a project_id")
    project = store.get(ctx.job.project_id)
    p = ctx.params
    brief = Brief(
        idea=p.get("idea") or project.idea,
        seconds=int(p.get("seconds", 60)),
        audience=p.get("audience", "children 4-8"),
        language=p.get("language", "English"),
        aspect=p.get("aspect") or project.aspect,
        characters=list(p.get("characters") or []),
    )
    ctx.ensure_service("ollama")  # starts `ollama serve` if it isn't running
    provider = provider_factory(ctx)
    ctx.cleanups.append(provider.unload)
    ctx.progress(0.01, "writing script", force=True)
    script, report = write_script(provider, brief, use_review=bool(p.get("review", True)),
                                  progress=lambda f, m: ctx.progress(f, m))
    project = store.get(project.id)  # re-read: may have changed while we were writing
    project.title = project.title or script.title
    project.data["script"] = script.model_dump()
    # Problems we couldn't fix automatically: shown on the storyboard (per shot) for manual edits.
    project.data["script_warnings"] = [w.model_dump() for w in report.warnings]
    project.data["script_report"] = {"rounds": report.rounds, "history": report.history}
    store.save(project)
    return {"title": script.title, "shots": len(script.shots), "duration_s": script.duration_s,
            "rounds": report.rounds, "warnings": [str(w) for w in report.warnings]}
