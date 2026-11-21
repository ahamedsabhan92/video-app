import copy
import json

import pytest

from app.config import Settings
from app.pipeline import script as script_job
from app.providers.base import ScriptProvider
from app.queue import EventBus, JobDB, JobQueue, JobStatus
from app.script.checks import repair, rule_checks
from app.script.guards import Brief, asks_for_more_characters, famous_names, names_in_idea
from app.script.schema import Script, llm_schema
from app.script.writer import build_prompt, normalise, shot_duration, write_script


def check(script, brief):
    return [str(p) for p in rule_checks(repair(script, brief), brief)]
from app.store.projects import ProjectStore


VERBS = ["wakes", "stretches", "yawns", "sits", "stands", "walks", "washes", "brushes", "dresses",
         "eats", "drinks", "packs", "zips", "waves", "skips", "hops", "smiles", "laughs", "runs", "reads",
         "sings", "claps", "jumps", "points", "looks", "listens", "hums", "giggles", "nods", "spins"]


def shot(n, text=None, vp="Wide shot of Ella in bed", chars=("Ella",)):
    text = text or f"Ella {VERBS[n % len(VERBS)]} happily."
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
    """Routes calls by schema: drafts, review issues, order issues, shot patches."""
    def __init__(self, drafts, reviews=None, orders=None, patches=None):
        self.drafts, self.reviews = list(drafts), list(reviews or [])
        self.orders, self.patches = list(orders or []), list(patches or [])
        self.prompts, self.calls, self.unloaded = [], [], False

    def generate(self, prompt, *, system=None, json_schema=None, progress=None):
        self.prompts.append(prompt)
        if progress:
            progress(0.5, "writing")
        keys = list((json_schema or {}).get("properties", {}))
        if keys == ["issues"]:
            self.calls.append("review")
            return json.dumps({"issues": self.reviews.pop(0) if self.reviews else []})
        if keys == ["states", "issues"]:
            self.calls.append("order")
            return json.dumps({"states": [], "issues": self.orders.pop(0) if self.orders else []})
        if keys == ["shots"]:
            self.calls.append("patch")
            p = self.patches.pop(0)
            return p if isinstance(p, str) else json.dumps({"shots": p})
        self.calls.append("draft")
        d = self.drafts.pop(0)
        return d if isinstance(d, str) else json.dumps(d)

    def unload(self):
        self.unloaded = True


def patch(sid, **kw):
    base = {"id": sid, "narration": "Ella smiles.", "visual_prompt": "Medium shot of Ella smiling",
            "motion": "slow push in", "characters": ["Ella"], "location": "Ella's bedroom", "sfx": "",
            "on_screen_text": "", "delete": False}
    return {**base, **kw}


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
    (script_dict(n_shots=8), "Too short"),
    (script_dict(n_shots=30), "Too long"),
    (script_dict(learning=""), "learning_word"),
])
def test_check_finds_problem(d, needle):
    problems = check(normalise(Script.model_validate(d)), BRIEF)
    assert any(needle in p for p in problems), problems


def test_caps_and_name_casing_normalised():
    d = copy.deepcopy(GOOD)
    d["characters"][0]["name"] = "ELLA"
    d["scenes"][0]["shots"][0].update(narration="ELLA WASHES her face on TV", characters=["ELLA"])
    s = normalise(Script.model_validate(d), BRIEF)
    assert s.characters[0].name == "Ella" and s.shots[0].characters == ["Ella"]
    assert s.shots[0].narration == "Ella washes her face on TV"


@pytest.mark.parametrize("field, text, needle", [
    ("narration", "Ella waves goodbye to her family.", "s01: Only Ella may appear; rewrite without: family"),
    ("visual_prompt", "Close-up of Ella using the toilet", "not appropriate"),
])
def test_people_and_kids_safety_flagged(field, text, needle):
    d = copy.deepcopy(GOOD)
    d["scenes"][0]["shots"][0][field] = text
    assert any(needle in p for p in check(normalise(Script.model_validate(d), BRIEF), BRIEF))


def test_extra_characters_are_removed_from_cast():
    s = repair(normalise(Script.model_validate(script_dict(extra_char="Mia")), BRIEF), BRIEF)
    assert [c.name for c in s.characters] == ["Ella"]
    assert check(s, BRIEF) == []


def test_famous_names_flagged_when_others_allowed():
    b = Brief(idea="Ella and her friends play", seconds=60)
    d = script_dict(extra_char="Peppa")
    d["scenes"][0]["shots"][0]["narration"] = "Peppa jumps in a puddle."
    problems = check(normalise(Script.model_validate(d), b), b)
    assert any("famous" in p and not p.startswith("s") for p in problems)   # cast-level
    assert any(p.startswith("s01:") and "famous" in p for p in problems)    # shot-level


def test_duplicate_action_flagged():
    d = copy.deepcopy(GOOD)
    d["scenes"][0]["shots"][1]["narration"] = d["scenes"][0]["shots"][0]["narration"]
    assert any(p.startswith("s02:") and "Repeats the action of s01" in p
               for p in check(normalise(Script.model_validate(d)), BRIEF))


def test_wordy_narration_flagged_for_kids():
    d = copy.deepcopy(GOOD)
    d["scenes"][0]["shots"][0]["narration"] = "Ella slowly and very sleepily opens her big brown eyes as the sun rises"
    assert any(p.startswith("s01:") and "Narration is over" in p for p in check(normalise(Script.model_validate(d)), BRIEF))


# ---- loop ---------------------------------------------------------------
def test_shot_problems_are_patched_not_rewritten():
    bad = copy.deepcopy(GOOD)
    bad["scenes"][-1]["shots"][-1]["narration"] = "She waves goodbye to her teacher."
    llm = FakeLLM([bad], patches=[[patch("s17", narration="Ella walks into class.")]])
    s, rep = write_script(llm, BRIEF)
    assert llm.calls == ["draft", "patch", "review", "order"]
    assert s.shots[-1].narration == "Ella walks into class." and rep.warnings == []
    assert [h["mode"] for h in rep.history] == ["draft", "patch"]
    assert "s17:" in llm.prompts[1] and "teacher" in llm.prompts[1]


def test_patch_can_delete_a_shot():
    llm = FakeLLM([script_dict(n_shots=19)], orders=[[{"shot": "s05", "problem": "Repeats s04; delete it."}]],
                  patches=[[patch("s05", delete=True)]])
    s, rep = write_script(llm, BRIEF)
    assert len(s.shots) == 18 and [sh.id for sh in s.shots][-1] == "s18"  # renumbered
    assert rep.warnings == []


def test_whole_script_problem_triggers_full_rewrite():
    llm = FakeLLM(["not json", script_dict(n_shots=8), GOOD])
    s, rep = write_script(llm, BRIEF, use_review=False)
    assert len(s.shots) == 17 and rep.rounds == 3 and rep.warnings == []
    assert [h["mode"] for h in rep.history] == ["draft", "rewrite", "rewrite"]
    assert "Too short" in llm.prompts[2] and "previous draft" in llm.prompts[2]


def test_order_issue_with_bad_shot_id_becomes_script_level():
    llm = FakeLLM([GOOD, GOOD], orders=[[{"shot": "s99", "problem": "Scenes out of order."}], []])
    s, rep = write_script(llm, BRIEF)
    assert [h["mode"] for h in rep.history] == ["draft", "rewrite"]


def test_leftover_problems_become_warnings():
    issue = [{"shot": "s04", "problem": "Kicks off blanket after already standing."}]
    llm = FakeLLM([GOOD], orders=[issue, issue, issue],
                  patches=[[patch("s04")], [patch("s04")]])
    s, rep = write_script(llm, BRIEF)
    assert rep.rounds == 3
    assert [(w.shot, w.source) for w in rep.warnings] == [("s04", "order")]


def test_failed_patch_keeps_script_and_retries():
    issue = [{"shot": "s03", "problem": "Order."}]
    llm = FakeLLM([GOOD], orders=[issue, []], patches=["not json", [patch("s03")]])
    s, rep = write_script(llm, BRIEF)
    assert [h["mode"] for h in rep.history] == ["draft", "patch", "patch"] and rep.warnings == []


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
    assert job.result["shots"] == 17 and job.result["warnings"] == []
    assert "ONLY Ella" in llm.prompts[0]
    assert llm.unloaded  # model freed after the job
    data = store.get(proj.id).data
    assert data["script"]["music"]["mood"] == "cheerful" and data["script_report"]["rounds"] == 1
    assert data["script_warnings"] == []
