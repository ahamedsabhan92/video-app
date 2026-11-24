import subprocess

from app.pipeline import animate as A
from app.providers.base import VideoProvider
from app.providers.comfyui_video import VideoSettings, wan_workflow
from app.queue import JobStatus
from app.render.clip import fit_clip, join, last_frame
from app.render.ffmpeg import duration, probe
from app.script.schema import CastEntry, Line, Shot
from app.store.projects import ProjectStore
from app.story.motion import motion_prompt
from test_story import _job, _project, _settings, make_cast


def _clip(path, seconds=1.0, size="320x180"):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=s={size}:r=24:d={seconds}", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def test_workflow_is_wired_and_frames_are_4k_plus_1():
    g = wan_workflow("p", "a.png", s=A.LEVELS[0], seed=3)
    for node in g.values():
        for v in node["inputs"].values():
            if isinstance(v, list):
                assert v[0] in g
    assert all(s.frames % 4 == 1 and s.width % 32 == 0 and s.height % 32 == 0 for s in A.LEVELS)
    assert A.settings_for(2, "9:16").height > A.settings_for(2, "9:16").width


def test_fit_clip_gives_exact_length_and_size(tmp_path):
    src = _clip(tmp_path / "a.mp4", 1.0)
    for secs, aspect, mode in ((0.6, "16:9", "cover"), (1.5, "16:9", "cover"), (2.5, "9:16", "blurpad")):
        out = fit_clip(src, tmp_path / f"o{secs}.mp4", seconds=secs, aspect=aspect, mode=mode)
        v = probe(out)["streams"][0]
        assert (v["width"], v["height"]) == ((1920, 1080) if aspect == "16:9" else (1080, 1920))
        assert abs(duration(out) - secs) < 0.06 and v["pix_fmt"] == "yuv420p"


def test_pieces_continue_from_the_last_frame(tmp_path):
    a, b = _clip(tmp_path / "a.mp4"), _clip(tmp_path / "b.mp4")
    assert last_frame(a, tmp_path / "l.png").exists()
    assert abs(duration(join([a, b], tmp_path / "j.mp4")) - (2.0 - 1 / 24)) < 0.1
    s = VideoSettings(832, 480, 65)
    assert [A.pieces_for(x, s) for x in (2.9, 4.6, 7.5, 60)] == [1, 1, 2, A.MAX_PIECES]


def test_motion_prompt_says_who_does_what(tmp_path):
    _, cast = make_cast(tmp_path)
    by = {c.name: c for c in cast}
    sh = Shot(id="s", framing="wide", motion="slow push in", lines=[Line(speaker="Fox", text="Hello!")],
              cast=[CastEntry(name="Fox", action="sits on the grass", expression="sly"), CastEntry(name="Crow", action="perched", holding="cheese in her beak")])
    p = motion_prompt(sh, by, "3d-cartoon")
    assert "Fox, the red fox: sits on the grass; talks" in p and "keeps holding cheese in her beak" in p and "camera slowly pushes in" in p
    sh.animation = "The fox jumps once"
    assert "The fox jumps once." in motion_prompt(sh, by, "3d-cartoon") and "sits on the grass" not in motion_prompt(sh, by, "3d-cartoon")


class FakeVideo(VideoProvider):
    name = "fake"

    def __init__(self, fail=()):
        self.calls, self.fail = [], list(fail)

    def animate(self, image, out, *, motion_prompt, seconds, seed=None, progress=None, settings=None, timeout_s=None):
        self.calls.append((image.name, settings.label(), seed))
        if self.fail and self.fail[0] == len(self.calls):
            self.fail.pop(0)
            raise RuntimeError("out of memory")
        return _clip(out, 1.0)

    def unload(self):
        pass


def _ready(tmp_path):
    p = _project(tmp_path)
    store = ProjectStore(_settings(tmp_path).projects_dir)
    for sid in ("s01", "s02"):
        f = store.assets_dir(p.id) / "images" / f"{sid}.png"
        f.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=320x180", "-frames:v", "1", str(f)], check=True)
        p.data.setdefault("images", {})[sid] = {"path": f"assets/images/{sid}.png", "prompt": "x", "take": 0, "seed": 1}
    return store, store.save(p)


async def test_animates_every_shot_once_and_again_when_the_picture_changes(tmp_path, monkeypatch):
    store, p = _ready(tmp_path)
    fake = FakeVideo()
    monkeypatch.setattr(A, "provider_factory", lambda ctx: fake)
    job, p = await _job(tmp_path, "animate", p)
    assert job.status == JobStatus.done, job.error_detail
    assert job.result["made"] == ["s01", "s02"] and set(p.data["clips"]) == {"s01", "s02"} and p.data["clips"]["s01"]["took_s"] >= 0
    n = len(fake.calls)
    job, p = await _job(tmp_path, "animate", p)
    assert job.result["made"] == [] and len(fake.calls) == n
    p.data["images"]["s02"]["take"] = 1
    store.save(p)
    job, p = await _job(tmp_path, "animate", p)
    assert job.result["made"] == ["s02"]


async def test_failure_is_retried_once_safer_then_skipped(tmp_path, monkeypatch):
    _, p = _ready(tmp_path)
    fake = FakeVideo(fail=[1])                       # first try of s01 fails, the safer one works
    monkeypatch.setattr(A, "provider_factory", lambda ctx: fake)
    job, p = await _job(tmp_path, "animate", p, {"level": 1})
    assert job.result["made"] == ["s01", "s02"] and p.data["clips"]["s01"]["attempt"] == 2
    assert fake.calls[0][1] == A.LEVELS[1].label() and fake.calls[1][1] == A.LEVELS[2].label()
    fake2 = FakeVideo(fail=[1, 2])                   # both tries fail: reported, the night goes on
    monkeypatch.setattr(A, "provider_factory", lambda ctx: fake2)
    job, p = await _job(tmp_path, "animate", p, {"force": True, "level": 1})
    assert job.status == JobStatus.done and list(job.result["failed"]) == ["s01"] and job.result["made"] == ["s02"]
    assert "out of memory" in p.data["clip_errors"]["s01"]


async def test_render_uses_clips(tmp_path, monkeypatch):
    store, p = _ready(tmp_path)
    monkeypatch.setattr(A, "provider_factory", lambda ctx: FakeVideo())
    _, p = await _job(tmp_path, "animate", p, {"shot_ids": ["s02"]})
    job, p = await _job(tmp_path, "render", p, {"no_copy": True})
    assert job.status == JobStatus.done, job.error_detail
    assert job.result["animated"] == ["s02"]
    job, _ = await _job(tmp_path, "render", p, {"no_copy": True, "no_clips": True})
    assert job.result["animated"] == []
