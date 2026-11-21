import asyncio
import subprocess

from app.config import Settings
from app.pipeline.narration import fit_duration
from app.queue import EventBus, JobDB, JobQueue, JobStatus
from app.render.ffmpeg import loudness, probe
from app.render.subtitles import build_ass
from app.render.timeline import INTRO_S, XFADE_S, build_timeline
from app.script.schema import Script
from app.store.projects import ProjectStore

SCRIPT = {
    "title": "Test {Video}", "characters": [{"name": "Ella", "description": "girl"}],
    "music": {"mood": "cheerful", "tempo": "medium", "instruments": "piano"},
    "scenes": [
        {"id": "sc1", "title": "A", "learning_word": "WAKE UP", "shots": [
            {"id": "s01", "narration": "Ella wakes up.", "visual_prompt": "x", "motion": "slow push in", "duration_s": 3.0},
            {"id": "s02", "narration": "She stretches.", "visual_prompt": "x", "motion": "pan left", "duration_s": 3.0}]},
        {"id": "sc2", "title": "B", "learning_word": "BRUSH TEETH", "shots": [
            {"id": "s03", "narration": "She brushes her teeth.", "visual_prompt": "x", "duration_s": 3.5}]},
    ],
}


def _tl(tmp_path, voice=None):
    images = {f"s0{i}": {"path": f"assets/images/s0{i}.png"} for i in (1, 2, 3)}
    return build_timeline(Script.model_validate(SCRIPT), tmp_path, images, voice or {})


def test_timeline_starts_clip_lengths_and_cues(tmp_path):
    tl = _tl(tmp_path, {"s01": {"path": "assets/voice/s01.wav", "seconds": 1.2}})
    assert [s.start for s in tl.segments] == [0.0, INTRO_S, INTRO_S + 3.0, INTRO_S + 6.0]
    assert [s.clip_len for s in tl.segments] == [INTRO_S + XFADE_S, 3.0 + XFADE_S, 3.0 + XFADE_S, 3.5]
    assert tl.duration == INTRO_S + 9.5
    learning = [c for c in tl.cues if c.kind == "learning"]
    assert [c.text for c in learning] == ["WAKE UP", "BRUSH TEETH"]
    assert learning[0].start < INTRO_S + 0.5 and learning[0].end > INTRO_S + 5.5   # spans both shots
    cap1 = next(c for c in tl.cues if c.kind == "caption" and c.text == "Ella wakes up.")
    assert cap1.end < INTRO_S + 3.0  # ends with the voice, inside its shot


def test_ass_escapes_and_sizes(tmp_path):
    tl = _tl(tmp_path)
    ass = build_ass(tl, 1080, 1920, brand="My Channel")
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert "Test (Video)" in ass and "{Video}" not in ass  # braces would start ASS override tags
    assert "My Channel" in ass and ass.count("Dialogue:") == 1 + 3 + 2 + 1


def test_fit_duration_only_grows():
    assert fit_duration(3.0, 1.0) == 3.0
    assert fit_duration(3.0, 3.2) == 4.0   # 0.25 lead-in + 3.2 + 0.5 breath → 3.95 → 4.0


def _tone(path, seconds, freq):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=f={freq}:d={seconds}",
                    "-ar", "24000", "-ac", "1", str(path)], check=True)


async def test_render_job_end_to_end(tmp_path):
    settings = Settings(projects_dir=tmp_path / "p", models_dir=tmp_path / "m", data_dir=tmp_path / "d",
                        comfyui_dir=tmp_path / "c", ollama_url="", comfyui_url="")
    store = ProjectStore(settings.projects_dir)
    proj = store.create("R", aspect="16:9")
    proj.data["script"] = SCRIPT
    proj.data["voice"] = {}
    for i, sid in enumerate(("s01", "s02", "s03")):
        _tone(store.assets_dir(proj.id) / "voice" / f"{sid}.wav", 1.5, 300 + 100 * i)
        proj.data["voice"][sid] = {"path": f"assets/voice/{sid}.wav", "seconds": 1.5}
    store.save(proj)
    db = JobDB(settings.db_path)
    q = JobQueue(db, EventBus(), settings)
    await q.start()
    assert (await q.wait(q.submit("placeholders", {}, project_id=proj.id).id)).status == JobStatus.done
    job = await q.wait(q.submit("render", {}, project_id=proj.id).id, timeout=300)
    assert job.status == JobStatus.done, job.error
    # second render reuses every cached clip (fast) and still succeeds
    again = await q.wait(q.submit("render", {}, project_id=proj.id).id, timeout=300)
    await q.stop()
    db.close()
    assert again.status == JobStatus.done
    pdir = store.assets_dir(proj.id).parent
    for aspect, size in (("16:9", (1920, 1080)), ("9:16", (1080, 1920))):
        f = pdir / job.result["exports"][aspect]["path"]
        streams = {s["codec_type"]: s for s in probe(f)["streams"]}
        assert (streams["video"]["width"], streams["video"]["height"]) == size
        assert streams["video"]["pix_fmt"] == "yuv420p" and streams["audio"]["sample_rate"] == "48000"
        assert abs(float(probe(f)["format"]["duration"]) - (INTRO_S + 9.5)) < 0.1
        lv = loudness(f)
        assert abs(lv["lufs"] - (-14)) <= 0.5 and lv["true_peak"] <= -1.0
