import json

from app.config import Settings
from app.images.prompting import compose, shot_seed, size_for
from app.pipeline import images as images_job
from app.providers.base import ImageProvider
from app.providers.comfyui_image import flux2_klein_workflow
from app.queue import EventBus, JobDB, JobQueue, JobStatus
from app.script.schema import Script
from app.store.projects import ProjectStore

SCRIPT = {
    "title": "T", "characters": [{"name": "Ella", "description": "6-year-old girl, curly brown hair, blue sweater."}],
    "music": {"mood": "cheerful", "tempo": "medium", "instruments": "piano"},
    "scenes": [{"id": "sc1", "title": "Wake", "learning_word": "WAKE UP", "shots": [
        {"id": "s01", "narration": "Ella wakes up.", "visual_prompt": "Wide shot of Ella in bed.",
         "characters": ["Ella"], "location": "bedroom", "duration_s": 3.0},
        {"id": "s02", "narration": "She stretches.", "visual_prompt": "Close-up of Ella stretching",
         "characters": ["Ella"], "location": "bedroom", "duration_s": 3.0},
    ]}],
}


def test_compose_includes_look_place_style_no_text():
    s = Script.model_validate(SCRIPT)
    p = compose(s.shots[0], s, "3d-cartoon")
    assert p.startswith("Wide shot of Ella in bed. Ella: 6-year-old girl, curly brown hair, blue sweater.")
    assert "Setting: bedroom." in p and "stylised 3D animated render" in p and "No text" in p


def test_sizes_are_multiples_of_16():
    for aspect in ("16:9", "9:16", "1:1", "4:5"):
        w, h = size_for(aspect)
        assert w % 16 == 0 and h % 16 == 0 and 0.85e6 < w * h < 1.15e6


def test_seed_stable_per_shot_and_take():
    assert shot_seed("p", "s01") == shot_seed("p", "s01")
    assert shot_seed("p", "s01") != shot_seed("p", "s01", take=1) != shot_seed("p", "s02")


def test_workflow_graph_is_wired():
    g = flux2_klein_workflow("a cat", width=1360, height=768, seed=7)
    assert g["2"]["inputs"]["type"] == "flux2" and g["10"]["inputs"]["cfg"] == 1.0
    assert g["7"]["inputs"] == {"steps": 4, "width": 1360, "height": 768}
    for node in g.values():  # every link points at an existing node
        for v in node["inputs"].values():
            if isinstance(v, list):
                assert v[0] in g


class FakeImages(ImageProvider):
    def __init__(self):
        self.calls, self.unloaded = [], False

    def generate(self, prompt, out, *, width, height, references=None, seed=None, progress=None):
        self.calls.append({"prompt": prompt, "seed": seed, "size": (width, height), "out": out.name})
        if progress:
            progress(None, "loading")
            progress(0.5, "sampling 2/4")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x89PNG fake")
        return out

    def unload(self):
        self.unloaded = True


async def _run(tmp_path, monkeypatch, fake, params, project=None):
    settings = Settings(projects_dir=tmp_path / "p", models_dir=tmp_path / "m", data_dir=tmp_path / "d",
                        comfyui_dir=tmp_path / "c", ollama_url="", comfyui_url="")
    monkeypatch.setattr(images_job, "provider_factory", lambda ctx: fake)
    store = ProjectStore(settings.projects_dir)
    if project is None:
        project = store.create("T", style="watercolour", aspect="9:16")
        project.data["script"] = SCRIPT
        store.save(project)
    db = JobDB(tmp_path / f"engine-{len(fake.calls)}.db")
    q = JobQueue(db, EventBus(), settings)
    await q.start()
    job = await q.wait(q.submit("images", params, project_id=project.id).id)
    await q.stop()
    db.close()
    return job, store, store.get(project.id)


async def test_images_job_makes_one_per_shot_and_records_meta(tmp_path, monkeypatch):
    fake = FakeImages()
    job, store, proj = await _run(tmp_path, monkeypatch, fake, {})
    assert job.status == JobStatus.done, job.error
    assert job.result["made"] == ["s01", "s02"] and job.result["size"] == [768, 1360]
    assert (store.assets_dir(proj.id) / "images" / "s01.png").exists()
    assert proj.data["images"]["s02"]["path"] == "assets/images/s02.png"
    assert "watercolour" in fake.calls[0]["prompt"] and fake.unloaded


async def test_existing_images_skipped_force_makes_new_take(tmp_path, monkeypatch):
    fake = FakeImages()
    _, store, proj = await _run(tmp_path, monkeypatch, fake, {})
    first_seed = proj.data["images"]["s01"]["seed"]
    job, _, _ = await _run(tmp_path, monkeypatch, fake, {}, project=proj)
    assert job.result["made"] == [] and job.result["skipped"] == ["s01", "s02"]
    job, _, proj = await _run(tmp_path, monkeypatch, fake, {"shot_ids": ["s01"], "force": True}, project=proj)
    assert job.result["made"] == ["s01"]
    assert proj.data["images"]["s01"]["take"] == 1 and proj.data["images"]["s01"]["seed"] != first_seed


async def test_images_job_needs_a_script(tmp_path, monkeypatch):
    settings_store = ProjectStore(tmp_path / "p")
    proj = settings_store.create("Empty")
    job, _, _ = await _run(tmp_path, monkeypatch, FakeImages(), {}, project=proj)
    assert job.status == JobStatus.failed and "no script" in job.error


async def test_placeholder_images_get_replaced(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / "p")
    proj = store.create("T", aspect="16:9")
    proj.data["script"] = SCRIPT
    img = store.assets_dir(proj.id) / "images" / "s01.png"
    img.parent.mkdir(parents=True)
    img.write_bytes(b"placeholder")
    proj.data["images"] = {"s01": {"path": "assets/images/s01.png", "placeholder": True}}
    store.save(proj)
    job, _, proj = await _run(tmp_path, monkeypatch, FakeImages(), {}, project=proj)
    assert job.result["made"] == ["s01", "s02"] and "placeholder" not in proj.data["images"]["s01"]
