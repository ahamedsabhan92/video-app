import json

import numpy as np
import pytest
import soundfile as sf

from app.characters.model import CharacterStore, Look, Outfit, Sheet, Version, Voice
from app.config import Settings
from app.images import story as S
from app.pipeline import images as images_job, narration as narration_job, story as story_job
from app.providers.base import ImageProvider, ScriptProvider, VoiceProvider
from app.queue import EventBus, JobDB, JobQueue, JobStatus
from app.render.timeline import INTRO_S, LEAD_IN_S, build_timeline
from app.script.schema import CastEntry, Line, Location, Scene, Script, Shot
from app.store.projects import ProjectStore
from app.story import writer as W
from app.story.text import split_units

STORY = """A Crow found some cheese. She flew to a branch.

"Good morning, Crow," said the Fox. "What a beautiful bird you are!"

The Crow was proud. She opened her beak and sang, "Caw! Caw!"

"Thank you," laughed the Fox."""


def _settings(tmp_path):
    return Settings(projects_dir=tmp_path / "projects", models_dir=tmp_path / "m", data_dir=tmp_path / "d",
                    comfyui_dir=tmp_path / "c", ollama_url="http://127.0.0.1:1", comfyui_url="http://127.0.0.1:1")


def make_cast(tmp_path, styles=("3d-cartoon",)):
    store = CharacterStore(_settings(tmp_path).characters_dir)
    out = []
    for name, species, voice in (("Crow", "crow", Voice(voice="af_bella", speed=1.0, pitch=2)), ("Fox", "red fox", Voice(voice="bm_george", speed=0.95, pitch=0))):
        c = store.create(name, "animal")
        c.fields, c.voice = {"species": species}, voice
        c.expressions = ["happy", "sad", "proud", "singing", "sly"]
        c.outfits = [Outfit(id="w1", name="Winter", description="a green knitted scarf")]
        for style in styles:
            d = store.dir(c.id) / "locked" / style
            d.mkdir(parents=True)
            views = {}
            for v in ("full_body", "front", "expr:proud"):
                f = d / f"{name.lower()}-{v.replace(':', '-')}.png"
                f.write_bytes(b"png")
                views[v] = str(f.relative_to(store.dir(c.id)))
            c.looks[style] = Look(versions=[Version(id="v1", path="x.png")], current="v1", locked=Sheet(version="v1", views=views))
        out.append(store.save(c))
    return store, out


# ---- shots from a pasted story -------------------------------------------------------
def test_groups_follow_paragraphs_and_keep_speech_together():
    units = split_units(STORY, ["Crow", "Fox"])
    groups = [[u.text for u in g] for g in W.group_units(units, [1])]
    assert groups == [["A Crow found some cheese.", "She flew to a branch."],
                      ["Good morning, Crow,", "said the Fox.", "What a beautiful bird you are!"],
                      ["The Crow was proud.", "She opened her beak and sang,", "Caw!", "Caw!"],
                      ["Thank you,", "laughed the Fox."]]
    # even if the AI wants a new shot at every sentence, nothing tiny or torn apart comes out
    every = W.group_units(units, [u.n for u in units])
    assert ["Thank you,", "laughed the Fox."] in [[u.text for u in g] for g in every]
    assert all(sum(len(u.text.split()) for u in g) >= 4 for g in every)


class FakeLLM(ScriptProvider):
    def __init__(self, answers):
        self.answers, self.asked, self.unloaded = answers, [], False

    def generate(self, prompt, *, system=None, json_schema=None, progress=None):
        keys = list(json_schema["properties"])
        kind = {"starts": "split", "title": "draft", "logline": "plan", "shots": "detail"}[keys[0]]
        self.asked.append((kind, prompt))
        a = self.answers[kind]
        a = a.pop(0) if isinstance(a, list) and len(a) > 1 else (a[0] if isinstance(a, list) else a)
        return a if isinstance(a, str) else json.dumps(a(prompt, json_schema) if callable(a) else a)

    def unload(self):
        self.unloaded = True


PLAN = {"logline": "A fox flatters a crow.", "music": {"mood": "playful", "tempo": "medium", "instruments": "flute"},
        "locations": [{"name": "Old Oak", "description": "an old oak tree on the right, green grass"}],
        "shots": [], "props": [{"name": "cheese", "look": "a big wedge of yellow cheese", "gone_after": "s04",
                                "held": [{"by": "Crow", "how": "in her beak", "first_shot": "s01", "last_shot": "s03"}]}]}


def detail(prompt, schema):
    ids = schema["properties"]["shots"]["items"]["properties"]["id"]["enum"]
    cast = {"s01": [("Crow", "sits on the branch")], "s02": [("Crow", "looks down"), ("Fox", "sits on the grass")],
            "s03": [("Crow", "opens her beak wide, singing")], "s04": [("Fox", "grins"), ("Crow", "looks shocked")]}
    return {"shots": [{"id": i, "location": "Old Oak", "framing": "close-up" if i == "s02" else "medium", "focus": "upper left",
                       "cast": [{"name": n, "action": a, "expression": "proud", "outfit": "Winter"} for n, a in cast[i]],
                       "extra": "the cheese in the air", "motion": "slow push in"} for i in ids]}


def test_paste_keeps_exact_words_and_plans_pictures(tmp_path):
    _, cast = make_cast(tmp_path)
    llm = FakeLLM({"split": {"starts": [1], "dialogue": [{"unit": 8, "speaker": "Crow", "emotion": "proud"}]}, "plan": PLAN, "detail": detail})
    script, rep = W.write_story(llm, mode="paste", title="The Crow and the Fox", text=STORY, cast=cast)
    assert rep.exact is None and [s.id for s in script.shots] == ["s01", "s02", "s03", "s04"]
    s = {x.id: x for x in script.shots}
    assert [(l.speaker, l.text) for l in s["s02"].lines] == [("Fox", "Good morning, Crow,"), ("Narrator", "said the Fox."), ("Fox", "What a beautiful bird you are!")]
    assert s["s03"].lines[-1].speaker == "Crow" and s["s03"].lines[-1].text == "Caw! Caw!"     # one quote, one speaker
    assert s["s01"].cast[0].holding == "a big wedge of yellow cheese in her beak"
    assert s["s03"].cast[0].holding == "" and s["s03"].cast[0].expression == "singing"          # can't sing with it
    assert s["s02"].framing == "medium"                                                         # two characters: no close-up
    assert s["s01"].framing == "wide"
    assert all(c.outfit == "" for x in script.shots for c in x.cast)                            # the story mentions no scarf
    assert s["s01"].visual_prompt == "" and s["s03"].visual_prompt == "the cheese in the air"   # held there / loose here


def test_outfit_kept_when_the_story_mentions_it(tmp_path):
    _, cast = make_cast(tmp_path)
    llm = FakeLLM({"split": {"starts": [1], "dialogue": []}, "plan": {**PLAN, "props": []}, "detail": detail})
    script, _ = W.write_story(llm, mode="paste", title="T", text="It was winter and the Crow sat down.", cast=cast)
    assert script.shots[0].cast[0].outfit == "Winter"


def test_unknown_speaker_becomes_a_warning_not_a_changed_word(tmp_path):
    _, cast = make_cast(tmp_path)
    llm = FakeLLM({"split": {"starts": [1], "dialogue": []}, "plan": PLAN, "detail": detail})
    script, rep = W.write_story(llm, mode="paste", title="T", text='A noise. "Who is there?" It was dark.', cast=cast)
    assert rep.exact is None and any("Who says" in w.message for w in rep.warnings)


def test_broken_ai_replies_still_give_an_editable_story(tmp_path):
    _, cast = make_cast(tmp_path)
    llm = FakeLLM({"split": "not json", "plan": "{", "detail": "nope"})
    script, rep = W.write_story(llm, mode="paste", title="T", text=STORY, cast=cast)
    assert rep.exact is None and len(script.shots) >= 4 and rep.warnings


def test_idea_draft_is_checked_and_rewritten(tmp_path):
    _, cast = make_cast(tmp_path)
    bad = {"title": "T", "shots": [{"lines": [{"speaker": "Narrator", "text": "Short.", "emotion": ""}]}]}
    good = {"title": "T", "shots": [{"lines": [{"speaker": sp, "text": "Fox: “one two three four five six seven eight nine ten.”", "emotion": "Sly"}]}
                                    for sp in ["Narrator", "Crow", "Fox"] * 4 + ["Narrator"]]}
    llm = FakeLLM({"draft": [bad, good]})
    title, shots, probs, hist = W.draft_story(llm, "idea", 60, cast)
    assert len(shots) == 13 and not probs and len(hist) == 2
    assert shots[1][0].text == "one two three four five six seven eight nine ten." and shots[1][0].emotion == "sly"
    assert W._tidy([Line(speaker="Fox", text="All done. And then he went to the")], ["Fox"])[0].text == "All done."


def test_repeated_actions_are_reported():
    shots = [Shot(id=f"s0{i}", lines=[], cast=[CastEntry(name="Fox", action="sits")]) for i in range(1, 5)]
    sc = Script(title="T", scenes=[Scene(title="x", shots=shots)])
    assert [p.shot for p in W.picture_problems(sc, ["Fox"])] == ["s03", "s04"]


# ---- pictures --------------------------------------------------------------------------
def _script():
    return Script(title="T", locations=[Location(name="Old Oak", description="an old oak")], scenes=[Scene(title="Old Oak", shots=[
        Shot(id="s01", location="Old Oak", framing="medium", focus="upper left", duration_s=3, lines=[Line(speaker="Narrator", text="A crow."), Line(speaker="Fox", text="Hello!")],
             cast=[CastEntry(name="Fox", action="sits", expression="sly"), CastEntry(name="Crow", action="perched", holding="cheese in its beak", expression="proud")]),
        Shot(id="s02", location="Old Oak", framing="close-up", duration_s=3, lines=[Line(speaker="Crow", text="Caw!")], cast=[CastEntry(name="Crow", action="smiles", expression="proud")])])])


def test_passes_one_character_each_holder_first(tmp_path):
    store, cast = make_cast(tmp_path)
    by = {c.name: c for c in cast}
    shots = _script().shots
    a, b = S.story_passes(shots[0], by, store, "3d-cartoon")
    assert (a.name, b.name) == ("Crow", "Fox") and len(a.refs) == len(b.refs) == 1
    assert "Fox" not in a.prompt and "The beak is closed around it" in a.prompt
    assert "Keep everything else in image 1 exactly the same, including Crow and what Crow holds." in b.prompt and "Fox holds nothing" in b.prompt
    assert a.refs[0].name == "crow-full_body.png" and "about half" in a.prompt
    (c,) = S.story_passes(shots[1], by, store, "3d-cartoon")
    assert c.refs[0].name == "crow-expr-proud.png" and "close-up" in c.prompt       # the acted expression is the reference


def test_missing_sheet_and_unknown_names_are_explained(tmp_path):
    _, cast = make_cast(tmp_path)
    assert S.missing_references("watercolour", cast) == ["Crow has no locked character sheet in Watercolour", "Fox has no locked character sheet in Watercolour"]
    sc = _script()
    sc.shots[0].cast[0].name = "Foxy"
    assert "isn't one of this story's characters" in S.missing_references("3d-cartoon", cast, sc)[0]
    sc.shots[0].cast[0].name, sc.shots[0].cast[0].outfit = "Fox", "Winter"
    assert "no reference image" in S.missing_references("3d-cartoon", cast, sc)[0]


class FakeImages(ImageProvider):
    def __init__(self):
        self.calls = []

    def generate(self, prompt, out, *, width, height, references=None, seed=None, progress=None):
        import subprocess
        self.calls.append({"prompt": prompt, "refs": [p.name for p in references or []], "out": out.name, "seed": seed})
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=320x180", "-frames:v", "1", str(out)], check=True)
        return out

    def unload(self):
        pass


async def _job(tmp_path, kind, project, params=None):
    s = _settings(tmp_path)
    db = JobDB(tmp_path / "e.db")
    q = JobQueue(db, EventBus(), s)
    await q.start()
    job = await q.wait(q.submit(kind, params or {}, project_id=project.id).id, timeout=60)
    await q.stop()
    db.close()
    return job, ProjectStore(s.projects_dir).get(project.id)


def _project(tmp_path, **kw):
    _, cast = make_cast(tmp_path)
    store = ProjectStore(_settings(tmp_path).projects_dir)
    p = store.create("T", style="3d-cartoon", cast=[c.id for c in cast], second_aspect=None, **kw)
    p.data["script"] = _script().model_dump()
    return store.save(p)


async def test_images_job_makes_place_then_passes_and_redoes_only_what_changed(tmp_path, monkeypatch):
    p = _project(tmp_path)
    fake = FakeImages()
    monkeypatch.setattr(images_job, "provider_factory", lambda ctx: fake)
    job, p = await _job(tmp_path, "images", p)
    assert job.status == JobStatus.done, job.error_detail
    assert [c["out"] for c in fake.calls] == ["16x9-old-oak.png", "s01-1.png", "s01-2.png", "s02-1.png"]
    assert fake.calls[1]["refs"] == ["s01-0.png", "crow-full_body.png"] and fake.calls[2]["refs"] == ["s01-1.png", "fox-full_body.png"]
    assert "An empty place" in fake.calls[0]["prompt"] and set(p.data["images"]) == {"s01", "s02"}
    job, p = await _job(tmp_path, "images", p)
    assert job.result["made"] == [] and len(fake.calls) == 4
    p.data["script"]["scenes"][0]["shots"][1]["cast"][0]["action"] = "laughs"
    ProjectStore(_settings(tmp_path).projects_dir).save(p)
    job, p = await _job(tmp_path, "images", p)
    assert job.result["made"] == ["16:9:s02"] and len(fake.calls) == 5
    job, p = await _job(tmp_path, "images", p, {"plate": "Old Oak"})                         # a new take of the place…
    assert job.result["plates"] == ["Old Oak"] and len(fake.calls) == 6
    job, p = await _job(tmp_path, "images", p)
    assert sorted(job.result["made"]) == ["16:9:s01", "16:9:s02"]                              # …redoes the shots there


async def test_images_job_explains_a_missing_sheet(tmp_path, monkeypatch):
    p = _project(tmp_path)
    p.style = "anime"
    ProjectStore(_settings(tmp_path).projects_dir).save(p)
    monkeypatch.setattr(images_job, "provider_factory", lambda ctx: FakeImages())
    job, _ = await _job(tmp_path, "images", p)
    assert job.status == JobStatus.failed and "Crow has no locked character sheet in Anime" in job.error


def test_camera_crops(tmp_path):
    import subprocess
    plate = tmp_path / "p.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=1360x768", "-frames:v", "1", str(plate)], check=True)
    from app.render.ffmpeg import probe
    for f in ("wide", "medium", "close-up"):
        o = S.camera(plate, Shot(id="x", framing=f, focus="upper right", cast=[CastEntry(name="A")]), tmp_path / f"{f}.png", 1360, 768)
        st = probe(o)["streams"][0]
        assert (st["width"], st["height"]) == (1360, 768)
    assert (tmp_path / "wide.png").read_bytes() != (tmp_path / "medium.png").read_bytes()


# ---- voices + captions -------------------------------------------------------------------
class FakeVoice(VoiceProvider):
    def __init__(self):
        self.calls, self.speed = [], 1.0

    def speak(self, text, out, *, voice="", language="", progress=None):
        self.calls.append((text, voice, self.speed))
        out.parent.mkdir(parents=True, exist_ok=True)
        n = int(24000 * 0.3 * len(text.split()))
        sf.write(out, (0.1 * np.sin(np.arange(n) / 10)).astype("float32"), 24000, subtype="PCM_16")
        return out

    def unload(self):
        pass


async def test_each_speaker_has_its_own_voice_and_only_changed_lines_are_redone(tmp_path, monkeypatch):
    p = _project(tmp_path, narrator={"voice": "af_heart", "speed": 0.85, "pitch": 0})
    fake = FakeVoice()
    monkeypatch.setattr(narration_job, "provider_factory", lambda ctx, speed: fake)
    job, p = await _job(tmp_path, "narration", p)
    assert job.status == JobStatus.done, job.error_detail
    assert fake.calls == [("A crow.", "af_heart", 0.85), ("Hello!", "bm_george", 0.95), ("Caw!", "af_bella", 1.0)]
    v = p.data["voice"]["s01"]
    assert [l["speaker"] for l in v["lines"]] == ["Narrator", "Fox"] and v["lines"][1]["start"] > v["lines"][0]["seconds"]
    shots = Script.model_validate(p.data["script"]).shots
    assert shots[0].duration_s >= v["seconds"] + LEAD_IN_S and shots[1].duration_s >= 2.5
    await _job(tmp_path, "narration", p)
    assert len(fake.calls) == 3
    p.data["script"]["scenes"][0]["shots"][0]["lines"][1]["text"] = "Good morning!"
    ProjectStore(_settings(tmp_path).projects_dir).save(p)
    job, p = await _job(tmp_path, "narration", p)
    assert job.result["made"] == ["s01-2"] and fake.calls[-1][0] == "Good morning!"


@pytest.mark.parametrize("on", [True, False])
def test_dialogue_captions_toggle(tmp_path, on):
    images = {"s01": {"path": "a.png"}, "s02": {"path": "b.png"}}
    voice = {"s01": {"path": "v.wav", "seconds": 2.0, "lines": [{"start": 0.0, "seconds": 0.8}, {"start": 1.05, "seconds": 0.9}]}}
    tl = build_timeline(_script(), tmp_path, images, voice, dialogue_captions=on)
    caps = [(c.kind, c.text) for c in tl.cues if c.kind in ("caption", "dialogue")]
    assert ("caption", "A crow.") in caps and (("dialogue", "Fox: Hello!") in caps) == on
    if on:
        d = next(c for c in tl.cues if c.text == "Fox: Hello!")
        assert abs(d.start - (INTRO_S + LEAD_IN_S + 1.05)) < 0.01
    from app.render.subtitles import build_ass
    assert ("Fox: Hello!" in build_ass(tl, 1920, 1080)) == on


async def test_story_job_saves_script_and_exactness(tmp_path, monkeypatch):
    p = _project(tmp_path, story_mode="paste", story_text=STORY)
    llm = FakeLLM({"split": {"starts": [1], "dialogue": []}, "plan": PLAN, "detail": detail})
    monkeypatch.setattr(story_job, "provider_factory", lambda ctx: llm)
    job, p = await _job(tmp_path, "story", p)
    assert job.status == JobStatus.done, job.error_detail
    assert job.result["exact"] is True and p.data["story_report"]["exact"] is True and llm.unloaded
    from fastapi.testclient import TestClient
    from app.main import create_app
    with TestClient(create_app(_settings(tmp_path))) as c:
        sc = p.data["script"]
        sc["scenes"][0]["shots"][0]["lines"][0]["text"] = "A Crow found some bread."
        r = c.put(f"/api/projects/{p.id}/script", json={"script": sc}).json()
        assert r["data"]["story_report"]["exact"] is False and "cheese" in r["data"]["story_report"]["difference"]
        assert c.patch(f"/api/projects/{p.id}", json={"dialogue_captions": False}).json()["dialogue_captions"] is False


async def test_story_job_needs_cast_and_text(tmp_path):
    store = ProjectStore(_settings(tmp_path).projects_dir)
    p = store.create("T", story_mode="paste", story_text="x")
    job, _ = await _job(tmp_path, "story", p)
    assert job.status == JobStatus.failed and "pick at least one character" in job.error


def test_actions_never_contradict_the_prop_timeline():
    w = {"cheese"}
    assert W._without_props("stands on four legs, holding the cheese in his mouth, smiling", w) == "stands on four legs, smiling"
    assert W._without_props("sings loudly, the cheese still in her beak", w) == "sings loudly"
    assert W._without_props("walks away with the cheese in his mouth, tail flicking", w) == "walks away, tail flicking"
    assert W._without_props("jumps up and catches the cheese", w) == "jumps up and catches the cheese"     # an event stays


def test_place_is_carried_over_when_missing(tmp_path):
    _, cast = make_cast(tmp_path)
    lines = [[Line(speaker="Narrator", text="A.")], [Line(speaker="Narrator", text="B.")]]
    d = {"locations": PLAN["locations"], "shots": [
        {"id": "s01", "location": "Old Oak", "cast": [{"name": "Crow", "where": "on the long branch", "action": "sits"}]},
        {"id": "s02", "location": "Old Oak", "cast": [{"name": "Crow", "where": "", "action": "looks down"}]}]}
    script, _ = W.build_script("T", lines, d, cast)
    assert [s.cast[0].action for s in script.shots] == ["on the long branch, sits", "on the long branch, looks down"]


def test_ai_sees_outfits_only_when_the_story_mentions_them(tmp_path):
    _, cast = make_cast(tmp_path)
    assert "Winter" not in W.cast_sheet(cast, "A sunny day.") and "Winter" in W.cast_sheet(cast, "One cold winter day.")


def test_extra_has_no_characters_and_actions_no_quoted_speech(tmp_path):
    _, cast = make_cast(tmp_path)
    d = {"locations": PLAN["locations"], "shots": [{"id": "s01", "location": "Old Oak", "extra": "the Fox below looking up",
         "cast": [{"name": "Crow", "where": "on the branch", "action": "opens her beak and sings 'Caw! Caw!', wings spread"}]}]}
    script, _ = W.build_script("T", [[Line(speaker="Narrator", text="A.")]], d, cast)
    assert script.shots[0].visual_prompt == "" and script.shots[0].cast[0].action == "on the branch, opens her beak and sings, wings spread"


def test_later_characters_are_not_named_early_and_closeups_drop_the_spot(tmp_path):
    store, cast = make_cast(tmp_path)
    by = {c.name: c for c in cast}
    by["Fox"].fields["stance"] = "on four legs"
    sh = Shot(id="s", framing="wide", cast=[CastEntry(name="Fox", action="on the grass, looks up at the Crow"), CastEntry(name="Crow", action="on the branch, looks sad")])
    a, b = S.story_passes(sh, by, store, "3d-cartoon")
    assert (a.name, b.name) == ("Crow", "Fox") and "Fox" not in a.prompt         # the one looked at comes first
    assert "looks up at the Crow" in b.prompt and "All four paws of Fox are on the ground" in b.prompt
    both = Shot(id="s", framing="wide", cast=[CastEntry(name="Fox", action="looks at the Crow"), CastEntry(name="Crow", action="looks at the Fox")])
    a, b = S.story_passes(both, by, store, "3d-cartoon")
    assert "someone nearby" in a.prompt and "Crow" not in a.prompt
    close = Shot(id="s", framing="close-up", cast=[CastEntry(name="Crow", action="on the long branch, upper left, sings loudly")])
    assert "branch" not in S.story_passes(close, by, store, "3d-cartoon")[0].prompt and "sings loudly" in S.story_passes(close, by, store, "3d-cartoon")[0].prompt


def test_a_prop_that_is_gone_is_not_mentioned_again(tmp_path):
    _, cast = make_cast(tmp_path)
    lines = [[Line(speaker="Narrator", text=x)] for x in "ABCDE"]
    d = {"locations": PLAN["locations"], "props": [{"name": "cheese", "look": "yellow cheese", "gone_after": "",
         "held": [{"by": "Crow", "how": "in her beak", "first_shot": "s01", "last_shot": "s01"}]}],
         "shots": [{"id": f"s0{i}", "location": "Old Oak", "cast": [{"name": "Crow", "where": "on the branch", "action": "looks down where the cheese fell"}]} for i in range(1, 6)]}
    script, _ = W.build_script("T", lines, d, cast)
    assert "cheese" in script.shots[1].cast[0].action and "cheese" not in script.shots[4].cast[0].action


async def test_places_can_be_made_first(tmp_path, monkeypatch):
    p = _project(tmp_path)
    fake = FakeImages()
    monkeypatch.setattr(images_job, "provider_factory", lambda ctx: fake)
    job, p = await _job(tmp_path, "images", p, {"places_only": True})
    assert job.result["plates"] == ["Old Oak"] and len(fake.calls) == 1 and not p.data.get("images")
