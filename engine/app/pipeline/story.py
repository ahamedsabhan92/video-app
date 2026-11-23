"""'story' job (Story Builder): pasted story or idea + library characters → script with lines, places and cast.

params: seconds (idea mode). Everything else is on the project: cast, story_mode, story_text, style.
"""
from __future__ import annotations

from app.characters.model import Character, CharacterStore
from app.providers.ollama_script import OllamaScriptProvider
from app.queue import JobContext, register
from app.store.projects import Project, ProjectStore
from app.story.writer import write_story


def provider_factory(ctx: JobContext):
    return OllamaScriptProvider(ctx.settings.ollama_url, temperature=0.6, max_tokens=4500, repeat_penalty=1.08)


def story_cast(ctx_settings, project: Project) -> list[Character]:
    store = CharacterStore(ctx_settings.characters_dir)
    out = []
    for cid in project.cast:
        try:
            out.append(store.get(cid))
        except KeyError:
            raise ValueError("a character of this story is no longer in the library; pick the cast again")
    if len({c.name.lower() for c in out}) != len(out):
        raise ValueError("two characters in this story have the same name; rename one in Characters")
    return out


@register("story")
def story_job(ctx: JobContext) -> dict:
    store = ProjectStore(ctx.settings.projects_dir)
    project = store.get(ctx.job.project_id or "")
    cast = story_cast(ctx.settings, project)
    if not cast:
        raise ValueError("pick at least one character for the story")
    if not project.story_text.strip():
        raise ValueError("paste your story, or describe an idea")
    ctx.ensure_service("ollama")
    ctx.require_model("script")
    provider = provider_factory(ctx)
    ctx.cleanups.append(provider.unload)
    ctx.progress(0.01, "reading the story", force=True)
    title = project.title if project.title.lower() != "untitled story" else ""
    script, rep = write_story(provider, mode=project.story_mode or "idea", title=title, text=project.story_text, cast=cast,
                              seconds=int(ctx.params.get("seconds", 60)), aspect=project.aspect,
                              progress=lambda f, m: ctx.progress(f, m))
    project = store.get(project.id)
    if not title:
        project.title = script.title
    project.data["script"] = script.model_dump()
    project.data.setdefault("audience", "children 4-8")
    project.data["script_warnings"] = [w.model_dump() for w in rep.warnings]
    project.data["story_report"] = {"mode": project.story_mode, "exact": rep.exact is None if project.story_mode == "paste" else None,
                                    "difference": rep.exact, "history": rep.history}
    store.save(project)
    return {"title": script.title, "shots": len(script.shots), "duration_s": script.duration_s,
            "exact": project.data["story_report"]["exact"], "warnings": [str(w) for w in rep.warnings]}
