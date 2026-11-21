import shutil

from app.config import Settings
from app.pipeline import music as music_job
from app.providers.base import MusicProvider
from app.providers.comfyui_music import music_prompt, stable_audio_workflow
from app.queue import EventBus, JobDB, JobQueue, JobStatus
from app.store.projects import ProjectStore
from test_images import SCRIPT


def test_prompt_from_script_music_block():
    p = music_prompt("calm", "slow", "piano, soft strings")
    assert p.startswith("Calm children's video background music") and "piano, soft strings" in p
    assert "72 BPM" in p and "no vocals" in p


def test_workflow_graph_is_wired():
    g = stable_audio_workflow("calm piano", seconds=40.0, seed=7)
    assert g["2"]["inputs"]["type"] == "stable_audio"
    assert g["5"]["inputs"]["seconds"] == 40.0 and g["6"]["inputs"]["seed"] == 7
    assert g["7"]["inputs"]["vae"] == ["1", 2] and g["8"]["class_type"] == "SaveAudio"
    for node in g.values():  # every link points at an existing node
        for v in node["inputs"].values():
            if isinstance(v, list):
                assert v[0] in g


class FakeMusic(MusicProvider):
    def __init__(self, src):
        self.src, self.calls, self.unloaded = src, [], False

    def compose(self, prompt, out, *, seconds, seed=None, progress=None):
        self.calls.append({"prompt": prompt, "seconds": seconds, "seed": seed})
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.src, out)
        return out

    def unload(self):
        self.unloaded = True


async def _run(tmp_path, monkeypatch, fake, params, project=None):
    settings = Settings(projects_dir=tmp_path / "p", models_dir=tmp_path / "m", data_dir=tmp_path / "d",
                        comfyui_dir=tmp_path / "c", ollama_url="", comfyui_url="")
    monkeypatch.setattr(music_job, "provider_factory", lambda ctx: fake)
    store = ProjectStore(settings.projects_dir)
    if project is None:
        project = store.create("T", style="watercolour")
        project.data["script"] = SCRIPT
        store.save(project)
    db = JobDB(tmp_path / f"engine-{len(fake.calls)}.db")
    q = JobQueue(db, EventBus(), settings)
    await q.start()
    job = await q.wait(q.submit("music", params, project_id=project.id).id)
    await q.stop()
    db.close()
    return job, store.get(project.id)


async def test_music_job_makes_one_track_then_reuses_it(tmp_path, monkeypatch, tone_wav):
    fake = FakeMusic(tone_wav)
    job, proj = await _run(tmp_path, monkeypatch, fake, {})
    assert job.status == JobStatus.done, job.error
    assert proj.data["music_track"] == "assets/music/bed.flac" and proj.data["music"]["mood"] == "cheerful"
    assert fake.calls[0]["seconds"] == 6.0 + 2.5 + 3.0 and fake.unloaded  # shots + intro + tail
    job, proj = await _run(tmp_path, monkeypatch, fake, {}, project=proj)
    assert job.result["made"] is False and len(fake.calls) == 1        # same prompt → reused
    job, proj = await _run(tmp_path, monkeypatch, fake, {"force": True}, project=proj)
    assert len(fake.calls) == 2 and proj.data["music"]["take"] == 1
    assert fake.calls[0]["seed"] != fake.calls[1]["seed"]
