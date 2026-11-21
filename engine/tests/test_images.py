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
    assert "Wide shot of Ella in bed. Ella: 6-year-old girl, curly brown hair, blue sweater." in p
    assert "Setting: bedroom." in p and "family film look" in p and "unsigned. No signature, no watermark, no text" in p and p.startswith("Stylised 3D animated film still: ")


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
    assert job.result["made"] == ["9:16:s01", "9:16:s02"] and job.result["sizes"] == {"9:16": [768, 1360]}
    assert (store.assets_dir(proj.id) / "images" / "s01.png").exists()
    assert proj.data["images"]["s02"]["path"] == "assets/images/s02.png"
    assert "watercolour" in fake.calls[0]["prompt"] and fake.unloaded


async def test_existing_images_skipped_force_makes_new_take(tmp_path, monkeypatch):
    fake = FakeImages()
    _, store, proj = await _run(tmp_path, monkeypatch, fake, {})
    first_seed = proj.data["images"]["s01"]["seed"]
    job, _, _ = await _run(tmp_path, monkeypatch, fake, {}, project=proj)
    assert job.result["made"] == [] and job.result["skipped"] == ["9:16:s01", "9:16:s02"]
    job, _, proj = await _run(tmp_path, monkeypatch, fake, {"shot_ids": ["s01"], "force": True}, project=proj)
    assert job.result["made"] == ["9:16:s01"]
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
    assert job.result["made"] == ["16:9:s01", "16:9:s02"] and "placeholder" not in proj.data["images"]["s01"]


async def test_native_second_aspect_makes_two_images_per_shot(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / "p")
    proj = store.create("T", aspect="9:16", second_aspect="16:9", second_aspect_mode="native")
    proj.data["script"] = SCRIPT
    store.save(proj)
    fake = FakeImages()
    job, store, proj = await _run(tmp_path, monkeypatch, fake, {}, project=proj)
    assert job.result["made"] == ["9:16:s01", "9:16:s02", "16:9:s01", "16:9:s02"]
    assert [c["size"] for c in fake.calls] == [(768, 1360)] * 2 + [(1360, 768)] * 2
    assert "Vertical frame" in fake.calls[0]["prompt"] and "Vertical frame" not in fake.calls[2]["prompt"]
    assert proj.data["images_second"]["s01"]["path"] == "assets/images/16x9/s01.png"
    assert (store.assets_dir(proj.id) / "images" / "16x9" / "s01.png").exists()


def test_project_aspect_settings():
    from app.images.aspects import export_plans, image_sets
    from app.store.projects import Project
    p = Project(id="x", title="x", aspect="16:9")
    assert p.second_aspect == "9:16" and p.second_aspect_mode == "blurpad"        # default: both, fast
    assert [s.aspect for s in image_sets(p)] == ["16:9"]
    assert [(e.aspect, e.framing, e.images.key) for e in export_plans(p)] == [
        ("16:9", "cover", "images"), ("9:16", "blurpad", "images")]
    shorts = Project(id="x", title="x", aspect="9:16", second_aspect=None)          # Shorts-first, no extra
    assert [(e.aspect, e.framing) for e in export_plans(shorts)] == [("9:16", "cover")]
    native = Project(id="x", title="x", aspect="9:16", second_aspect="16:9", second_aspect_mode="native")
    assert [(e.aspect, e.framing, e.images.key) for e in export_plans(native)] == [
        ("9:16", "cover", "images"), ("16:9", "cover", "images_second")]
    assert Project(id="x", title="x", aspect="1:1").second_aspect is None
    assert Project(id="x", title="x", aspect="16:9", second_aspect="16:9").second_aspect is None


def test_every_style_leads_with_its_medium_and_differs():
    from app.styles import STYLES
    from app.script.schema import Script as S
    sc = S.model_validate(SCRIPT)
    prompts = {k: compose(sc.shots[0], sc, k) for k in STYLES}
    for k, p in prompts.items():
        assert p.startswith(STYLES[k]["lead"] + ": "), k
    assert len(set(prompts.values())) == len(STYLES)


def test_preview_picks_spread_and_size():
    from app.pipeline.images import preview_shots, preview_size
    d = json.loads(json.dumps(SCRIPT))
    d["scenes"][0]["shots"] = [dict(d["scenes"][0]["shots"][0], id=f"s{i:02d}") for i in range(1, 11)]
    picks = [s.id for s in preview_shots(Script.model_validate(d))]
    assert picks == ["s01", "s04", "s07", "s10"]
    assert preview_size("16:9") == (896, 512) and all(v % 16 == 0 for v in preview_size("9:16"))


async def test_style_preview_job_makes_four_and_keeps_project_style(tmp_path, monkeypatch):
    fake = FakeImages()
    settings = Settings(projects_dir=tmp_path / "p", models_dir=tmp_path / "m", data_dir=tmp_path / "d",
                        comfyui_dir=tmp_path / "c", ollama_url="", comfyui_url="")
    monkeypatch.setattr(images_job, "provider_factory", lambda ctx: fake)
    store = ProjectStore(settings.projects_dir)
    p = store.create("T", style="watercolour")
    d = json.loads(json.dumps(SCRIPT))
    d["scenes"][0]["shots"] = [dict(d["scenes"][0]["shots"][0], id=f"s{i:02d}") for i in range(1, 7)]
    p.data["script"] = d
    store.save(p)
    db = JobDB(tmp_path / "e.db")
    q = JobQueue(db, EventBus(), settings)
    await q.start()
    job = await q.wait(q.submit("style_preview", {"style": "anime"}, project_id=p.id).id)
    again = await q.wait(q.submit("style_preview", {"style": "anime"}, project_id=p.id).id)
    await q.stop(); db.close()
    assert job.status == JobStatus.done, job.error
    assert job.result["shots"] == ["s01", "s03", "s04", "s06"] and len(fake.calls) == 4
    assert again.result["made"] == []                    # cached until the script/style changes
    p = store.get(p.id)
    assert p.style == "watercolour" and set(p.data["style_previews"]["anime"]) == {"s01", "s03", "s04", "s06"}
    assert fake.calls[0]["prompt"].startswith("Japanese-style 2D anime illustration: ")
