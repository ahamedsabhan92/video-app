import copy
import json

import pytest

from app.config import Settings
from app.pipeline import script as script_job
from app.providers.base import ScriptProvider
from app.queue import EventBus, JobDB, JobQueue, JobStatus
from app.script.guards import Brief, asks_for_more_characters, famous_names, names_in_idea
from app.script.schema import Script, llm_schema
from app.script.writer import build_prompt, check, normalise, shot_duration, write_script
from app.store.projects import ProjectStore


def shot(n, text="Ella wakes up.", vp="Wide shot of Ella in bed", chars=("Ella",)):
    return {"narration": text, "visual_prompt": f"{vp} {n}", "motion": "none", "characters": list(chars),
            "location": "Ella's bedroom", "sfx": "", "on_screen_text": ""}


def script_dict(n_shots=17, extra_char=None, learning="WAKE UP"):
    chars = [{"name": "Ella", "description": "7-year-old girl, curly brown hair, yellow dress"}]
    if extra_char:
        chars.append({"name": extra_char, "description": "someone"})
    per = 3
    scenes = [{"title": f"Scene {i}", "learning_word": learning,
               "shots": [shot(i * per + j) for j in range(per) if i * per + j < n_shots]}
              for i in range((n_shots + per - 1) // per)]
    return {"title": "Ella's Morning", "logline": "x", "characters": chars,
            "music": {"mood": "cheerful", "tempo": "medium", "instruments": "ukulele, glockenspiel"},
            "scenes": scenes}


GOOD = script_dict()
BRIEF = Brief(idea="Ella's school morning routine for kids", seconds=60)


class FakeLLM(ScriptProvider):
    """Returns queued replies; review calls (recognised by schema) get `reviews` replies."""
    def __init__(self, drafts, reviews=None):
        self.drafts, self.reviews = list(drafts), list(reviews or [])
        self.prompts, self.unloaded = [], False

    def generate(self, prompt, *, system=None, json_schema=None, progress=None):
        self.prompts.append(prompt)
        if progress:
            progress(0.5, "writing")
        if json_schema and list(json_schema.get("properties", {})) == ["issues"]:
            return json.dumps({"issues": self.reviews.pop(0) if self.reviews else []})
        d = self.drafts.pop(0)
        return d if isinstance(d, str) else json.dumps(d)

    def unload(self):
        self.unloaded = True


# ---- guards -------------------------------------------------------------
def test_names_in_idea():
    assert names_in_idea("Ella's school morning routine for kids") == ["Ella"]
    assert names_in_idea("Make a video: Omar and Lina go camping") == ["Omar", "Lina"]
    assert names_in_idea("a rainy day story") == []


def test_asks_for_more_characters():
    assert not asks_for_more_characters("Ella's school morning routine for kids")
    assert asks_for_more_characters("Ella and her dad bake a cake")


def test_famous_names_whole_word():
    assert famous_names("Mr. Bean jumps on the bed") == ["Mr. Bean"]
    assert famous_names("she eats beans and a bluebell") == []


def test_brief_flags():
    assert BRIEF.kids and BRIEF.is_routine and BRIEF.only_named and BRIEF.characters == ["Ella"]
    p = build_prompt(BRIEF)
    assert "ONLY Ella" in p and "BRUSH TEETH" in p and "wake up" in p


# ---- normalise / check --------------------------------------------------
def test_normalise_ids_motion_learning_word():
    d = copy.deepcopy(GOOD)
    d["scenes"].insert(1, {"title": "Empty", "learning_word": "", "shots": [shot(0, vp="  ")]})
    d["scenes"][1]["shots"][0]["visual_prompt"] = "  "
    d["scenes"][0]["learning_word"] = "wake up"
    s = normalise(Script.model_validate(d))
    assert [sc.id for sc in s.scenes][:2] == ["sc1", "sc2"]
    assert [sh.id for sh in s.shots][:3] == ["s01", "s02", "s03"]
    assert all(sh.motion and sh.motion != "none" for sh in s.shots)
    assert s.scenes[0].learning_word == "WAKE UP"


def test_shot_duration_scales_with_words():
    assert shot_duration("one") == 3.0
    assert shot_duration("one two three four five six seven eight nine ten") == 5.0
    assert shot_duration(" ".join(["w"] * 40)) == 6.0


def test_llm_schema_requires_everything_and_single_mood():
    sch = llm_schema()
    assert "duration_s" not in sch["$defs"]["Shot"]["properties"]
    assert set(sch["$defs"]["Shot"]["required"]) == set(sch["$defs"]["Shot"]["properties"])
    assert "learning_word" in sch["$defs"]["Scene"]["required"]
    assert "enum" in sch["$defs"]["Music"]["properties"]["mood"]


def test_good_script_has_no_problems():
    assert check(normalise(Script.model_validate(copy.deepcopy(GOOD))), BRIEF) == []


@pytest.mark.parametrize("d, needle", [
    (script_dict(extra_char="Mia"), "Remove these characters"),
    (script_dict(extra_char="Peppa"), "famous"),
    (script_dict(n_shots=8), "Too short"),
    (script_dict(n_shots=30), "Too long"),
    (script_dict(learning=""), "learning_word"),
])
def test_check_finds_problem(d, needle):
    problems = check(normalise(Script.model_validate(d)), BRIEF)
    assert any(needle in p for p in problems), problems


def test_wordy_narration_flagged_for_kids():
    d = copy.deepcopy(GOOD)
    d["scenes"][0]["shots"][0]["narration"] = "Ella slowly and very sleepily opens her big brown eyes as the sun rises"
    assert any("Narration over" in p for p in check(normalise(Script.model_validate(d)), BRIEF))


# ---- loop ---------------------------------------------------------------
def test_revises_until_clean():
    llm = FakeLLM(["not json", script_dict(n_shots=8), GOOD])
    s, rep = write_script(llm, BRIEF, use_review=False)
    assert len(s.shots) == 17 and rep.rounds == 3 and rep.problems == []
    assert "Too short" in llm.prompts[2] and "previous draft" in llm.prompts[2]


def test_review_issues_trigger_revision():
    llm = FakeLLM([GOOD, GOOD], reviews=[["s06: Ella dries her face but never washed it."], []])
    s, rep = write_script(llm, BRIEF)
    assert rep.rounds == 2 and rep.problems == []
    assert "never washed" in llm.prompts[2]


def test_returns_best_draft_with_remaining_problems():
    llm = FakeLLM([script_dict(n_shots=8), script_dict(n_shots=8, extra_char="Mia"), script_dict(n_shots=8)])
    s, rep = write_script(llm, BRIEF, use_review=False)
    assert rep.rounds == 3 and len(rep.problems) == 1 and "Too short" in rep.problems[0]


def test_gives_up_when_never_valid():
    with pytest.raises(ValueError):
        write_script(FakeLLM(["{}", "{}", "{}"]), BRIEF)


# ---- job ----------------------------------------------------------------
async def test_script_job_saves_into_project(tmp_path, monkeypatch):
    settings = Settings(projects_dir=tmp_path / "p", models_dir=tmp_path / "m", data_dir=tmp_path / "d",
                        comfyui_dir=tmp_path / "c", ollama_url="", comfyui_url="")
    llm = FakeLLM([GOOD])
    monkeypatch.setattr(script_job, "provider_factory", lambda ctx: llm)
    store = ProjectStore(settings.projects_dir)
    proj = store.create("Ella", idea="Ella's school morning routine for kids")
    db = JobDB(settings.db_path)
    q = JobQueue(db, EventBus(), settings)
    await q.start()
    job = await q.wait(q.submit("script", {"seconds": 60}, project_id=proj.id).id)
    await q.stop()
    db.close()
    assert job.status == JobStatus.done, job.error
    assert job.result["shots"] == 17 and job.result["problems"] == []
    assert "ONLY Ella" in llm.prompts[0]
    assert llm.unloaded  # model freed after the job
    data = store.get(proj.id).data
    assert data["script"]["music"]["mood"] == "cheerful" and data["script_report"]["rounds"] == 1
