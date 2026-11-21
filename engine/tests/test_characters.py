import pytest
from fastapi.testclient import TestClient

from app.characters.model import CharacterStore
from app.characters.prompts import describe, look_prompt, view_prompt
from app.config import Settings
from app.main import create_app
from app.pipeline import characters as cjobs
from app.providers.base import ImageProvider
from app.queue import EventBus, JobDB, JobQueue, JobStatus


class FakeImages(ImageProvider):
    def __init__(self):
        self.calls, self.unloaded = [], False

    def generate(self, prompt, out, *, width, height, references=None, seed=None, progress=None):
        self.calls.append({"prompt": prompt, "refs": [p.name for p in references or []], "size": (width, height)})
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x89PNG fake " + prompt[:20].encode())
        return out

    def unload(self):
        self.unloaded = True


def _settings(tmp_path):
    return Settings(projects_dir=tmp_path / "projects", models_dir=tmp_path / "m", data_dir=tmp_path / "d",
                    comfyui_dir=tmp_path / "c", ollama_url="http://127.0.0.1:1", comfyui_url="http://127.0.0.1:1")


def fox(store):
    c = store.create("Fox", "animal")
    return store.update(c.id, {"fields": {"species": "red fox", "size": "slim", "colour": "orange-red fur",
                                          "markings": "white chest and tail tip", "eyes": "amber"},
                               "personality": "sly, clever, flattering", "other": "a sly smile"})


def test_describe_adapts_to_kind(tmp_path):
    store = CharacterStore(tmp_path / "characters")
    f = fox(store)
    d = describe(f)
    assert d.startswith("Fox: a slim red fox, orange-red fur, markings: white chest and tail tip, eyes: amber")
    h = store.update(store.create("Mia", "human").id, {"fields": {"age": "7", "gender": "girl", "hair": "black curly"}})
    assert describe(h).startswith("Mia: a 7-year-old girl, black curly hair")
    assert "character design reference" in look_prompt(f, "3d-cartoon") and "full body" in look_prompt(f, "3d-cartoon")
    assert view_prompt(f, "anime", "side")[1] == (1152, 896)


def test_store_pick_edit_undo_lock(tmp_path):
    store = CharacterStore(tmp_path / "characters")
    c = fox(store)
    look = c.look("3d-cartoon")
    look.candidates = [{"path": "looks/3d-cartoon/a.png", "seed": 1}, {"path": "looks/3d-cartoon/b.png", "seed": 2}]
    store.save(c)
    c = store.pick(c.id, "3d-cartoon", 1)
    v1 = c.looks["3d-cartoon"].current
    with pytest.raises(ValueError):
        store.undo(c.id, "3d-cartoon")            # first version has no parent
    with pytest.raises(ValueError):
        store.lock(c.id, "3d-cartoon")            # no sheet yet
    with pytest.raises(ValueError):
        store.update(c.id, {"looks": {}})         # looks only change through jobs/actions


async def _run(tmp_path, monkeypatch, fake, kind, params):
    s = _settings(tmp_path)
    monkeypatch.setattr(cjobs, "provider_factory", lambda ctx: fake)
    db = JobDB(tmp_path / f"e{len(fake.calls)}-{kind}.db")
    q = JobQueue(db, EventBus(), s)
    await q.start()
    job = await q.wait(q.submit(kind, params).id)
    await q.stop()
    db.close()
    assert job.status == JobStatus.done, job.error_detail
    return job


async def test_full_character_flow(tmp_path, monkeypatch):
    store = CharacterStore(_settings(tmp_path).characters_dir)
    c = fox(store)
    fake = FakeImages()
    p = {"character_id": c.id, "style": "3d-cartoon"}
    job = await _run(tmp_path, monkeypatch, fake, "character_looks", {**p, "count": 3})
    assert len(job.result["candidates"]) == 3 and fake.calls[0]["refs"] == [] and fake.calls[0]["size"] == (896, 1152)
    c = store.pick(c.id, "3d-cartoon", 2)
    base = c.looks["3d-cartoon"].version(c.looks["3d-cartoon"].current)

    job = await _run(tmp_path, monkeypatch, fake, "character_sheet", p)
    assert set(job.result["views"]) == {"full_body", "front", "three_quarter", "side", "expr:happy", "expr:sad", "expr:surprised", "expr:angry"}
    assert all(call["refs"] == [base.path.split("/")[-1]] for call in fake.calls[3:10])  # views use the look
    c = store.lock(c.id, "3d-cartoon")
    locked = c.looks["3d-cartoon"].locked
    assert all(v.startswith("locked/3d-cartoon/") for v in locked.views.values())

    job = await _run(tmp_path, monkeypatch, fake, "character_edit", {**p, "instruction": "add a red scarf"})
    c = store.get(c.id)
    look = c.looks["3d-cartoon"]
    assert look.current == job.result["version"] and look.version(look.current).parent == base.id
    assert "look changes: add a red scarf" in describe(c, "3d-cartoon")
    assert look.locked.version == base.id                       # lock unchanged until re-locked
    with pytest.raises(ValueError):
        store.lock(c.id, "3d-cartoon")                          # sheet is from the older version
    c = store.undo(c.id, "3d-cartoon")
    assert c.looks["3d-cartoon"].current == base.id and "red scarf" not in describe(c, "3d-cartoon")

    c = store.update(c.id, {"outfits": [{"name": "Winter", "description": "a green knitted scarf and hat"}]})
    oid = c.outfits[0].id
    job = await _run(tmp_path, monkeypatch, fake, "character_outfit", {**p, "outfit_id": oid})
    c = store.get(c.id)
    assert c.outfits[0].refs["3d-cartoon"] == job.result["path"] and "green knitted scarf" in fake.calls[-1]["prompt"]
    assert fake.calls[-1]["refs"] == [locked.views["full_body"].split("/")[-1]]
    c = store.update(c.id, {"outfits": [{"id": oid, "name": "Winter", "description": "changed"}]})
    assert c.outfits[0].refs["3d-cartoon"]                       # editing text keeps the generated ref

    job = await _run(tmp_path, monkeypatch, fake, "character_looks", {**p, "style": "watercolour", "from_style": "3d-cartoon", "count": 2})
    assert job.result["kind"] == "restyle" and fake.calls[-1]["refs"] == [locked.views["full_body"].split("/")[-1]]
    assert fake.calls[-1]["prompt"].startswith("Hand-painted watercolour storybook illustration: redraw the exact same character")


def test_api_photo_needs_consent_and_lists(tmp_path):
    with TestClient(create_app(_settings(tmp_path))) as client:
        c = client.post("/api/characters", json={"name": "Crow", "kind": "animal"}).json()
        r = client.post(f"/api/characters/{c['id']}/photo", content=b"\x89PNG", headers={"content-type": "image/png"})
        assert r.status_code == 400 and "permission" in r.json()["detail"]
        r = client.post(f"/api/characters/{c['id']}/photo", content=b"\x89PNG",
                        headers={"content-type": "image/png", "x-consent": "yes"})
        assert r.status_code == 200 and r.json()["photo"] == "photo.png"
        assert client.get(f"/api/characters/{c['id']}/files/photo.png").content == b"\x89PNG"
        assert client.get(f"/api/characters/{c['id']}/files/character.json").status_code == 404
        assert [x["name"] for x in client.get("/api/characters").json()] == ["Crow"]
        assert "species" in [f["key"] for f in client.get("/api/character-fields").json()["animal"]]
        assert client.delete(f"/api/characters/{c['id']}").status_code == 204
        assert client.get("/api/characters").json() == []


def test_pitch_shift_moves_frequency_not_length(tmp_path):
    import subprocess
    import numpy as np
    import soundfile as sf
    from app.audio import pitch_shift
    src = tmp_path / "a.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=1", "-ar", "24000", str(src)], check=True)
    out = pitch_shift(src, tmp_path / "b.wav", 12)
    x, sr = sf.read(out)
    peak = np.argmax(np.abs(np.fft.rfft(x))) * sr / len(x)
    assert abs(peak - 440) < 15 and abs(len(x) / sr - 1.0) < 0.05


def test_expression_views_are_separate_and_exaggerated(tmp_path):
    from app.characters.prompts import view_keys
    store = CharacterStore(tmp_path / "characters")
    c = store.update(fox(store).id, {"expressions": ["happy", "sad", "surprised", "angry", "sly"]})
    assert view_keys(c)[-5:] == ["expr:happy", "expr:sad", "expr:surprised", "expr:angry", "expr:sly"]
    p, size = view_prompt(c, "3d-cartoon", "expr:sly")
    assert "sly half-closed eyes" in p and "never a human face" in p and size == (896, 896)
    assert "unmistakable, exaggerated singing-in-the-rain" in view_prompt(c, "3d-cartoon", "expr:singing-in-the-rain")[0]


async def test_new_expression_added_to_locked_sheet(tmp_path, monkeypatch):
    store = CharacterStore(_settings(tmp_path).characters_dir)
    c = fox(store)
    c.look("3d-cartoon").candidates = [{"path": "looks/3d-cartoon/a.png", "seed": 1}]
    store.save(c)
    (store.dir(c.id) / "looks/3d-cartoon").mkdir(parents=True)
    (store.dir(c.id) / "looks/3d-cartoon/a.png").write_bytes(b"x")
    store.pick(c.id, "3d-cartoon", 0)
    fake = FakeImages()
    await _run(tmp_path, monkeypatch, fake, "character_sheet", {"character_id": c.id, "style": "3d-cartoon"})
    assert len(fake.calls) == 3 + 4                               # 3 views + 4 default expressions
    store.lock(c.id, "3d-cartoon")
    store.update(c.id, {"expressions": [*store.get(c.id).expressions, "proud"]})
    job = await _run(tmp_path, monkeypatch, fake, "character_sheet",
                     {"character_id": c.id, "style": "3d-cartoon", "missing_only": True})
    assert job.result["made"] == ["expr:proud"] and len(fake.calls) == 8
    locked = store.get(c.id).looks["3d-cartoon"].locked
    assert locked.views["expr:proud"].startswith("locked/3d-cartoon/")
    assert (store.dir(c.id) / locked.views["expr:proud"]).exists()
