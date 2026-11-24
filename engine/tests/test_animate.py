import subprocess

from app.pipeline import animate as A
from app.providers.base import VideoProvider
from app.providers.comfyui_video import NEGATIVE, VideoSettings, encode_workflow, prompt_key, wan_workflow
from app.queue import JobStatus
from app.render.clip import crop_picture, fit_clip, join
from app.render.ffmpeg import duration, probe
from app.script.schema import CastEntry, Line, Shot
from app.store.projects import ProjectStore
from app.story.motion import closer_shot, motion_prompt
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


def test_pieces_are_cut_together_and_trimmed(tmp_path):
    a, b = _clip(tmp_path / "a.mp4"), _clip(tmp_path / "b.mp4")
    assert abs(duration(join([a, b], tmp_path / "j.mp4")) - 2.0) < 0.1
    out = fit_clip([a, b], tmp_path / "two.mp4", seconds=2.0, trim=0.2, cuts=[0.75])
    assert abs(duration(out) - 2.0) < 0.06
    for secs in (0.5, 2.5):                          # cut short / the last frame is held
        assert abs(duration(fit_clip(a, tmp_path / f"t{secs}.mp4", seconds=secs, trim=0.5)) - secs) < 0.06
    pic = tmp_path / "p.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=640x360", "-frames:v", "1", str(pic)], check=True)
    v = probe(crop_picture(pic, tmp_path / "c.png", .5, .92, .97))["streams"][0]
    assert (v["width"], v["height"]) == (320, 180)


def _frames(path, pix="gray"):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", "scale=160:90", "-f", "rawvideo", "-pix_fmt", pix, "-"], capture_output=True, check=True).stdout
    return [raw[i:i + 160 * 90] for i in range(0, len(raw), 160 * 90)]


def test_every_frame_is_shown_once_forwards_at_the_clips_own_rate(tmp_path):
    """No slow motion, no in-between frames, nothing backwards: frame n of the result is frame n of the clip."""
    src = tmp_path / "grey.mp4"                      # brightness = frame number
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=s=320x180:r=24:d=2,geq=lum='16+4*N':cb=128:cr=128", "-pix_fmt", "yuv420p", "-crf", "1", str(src)], check=True)
    out = fit_clip(src, tmp_path / "o.mp4", seconds=2.0, trim=0.5)
    assert probe(out)["streams"][0]["r_frame_rate"] == "24/1"
    v = [f[len(f) // 2] for f in _frames(out)]
    assert len(v) == 48 and all(3 <= b - a <= 7 for a, b in zip(v[:35], v[1:36]))        # every frame, in order
    assert max(v[36:]) - min(v[36:]) <= 1                                                # then held
    two = [f[len(f) // 2] for f in _frames(fit_clip([src, src], tmp_path / "two.mp4", seconds=2.0, trim=0.5, cuts=[1.25]))]
    assert two[30] < 24 and two[29] > 110                                                # the second piece starts exactly at its cut


def test_the_camera_move_is_added_smoothly(tmp_path):
    from app.render.motion import Move, clip_move
    assert clip_move("slow push in").z1 > 1 and clip_move("") == clip_move("steady") == clip_move("she waves") == Move(1.0, 1.0)
    src = tmp_path / "s.mp4"                         # a still picture: all movement is the camera's
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=832x480:r=24:d=2.5,trim=end_frame=1,loop=59:1,setpts=N/24/TB", "-pix_fmt", "yuv420p", "-crf", "4", str(src)], check=True)
    for motion, moves in (("slow push in", True), ("pan left", True), ("steady", False)):
        fr = _frames(fit_clip(src, tmp_path / "o.mp4", seconds=2.0, motion=motion))
        diff = [sum(abs(x - y) for x, y in zip(a, b)) / len(a) for a, b in zip(fr, fr[1:])]
        assert (max(diff) > .3) == moves
        if moves:                                    # no jumps: neighbouring steps are alike
            assert max(abs(a - b) for a, b in zip(diff, diff[1:])) < .35 * max(diff)


def test_a_trembling_clip_is_steadied(tmp_path):
    pic, src = tmp_path / "p.png", tmp_path / "s.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "mandelbrot=s=900x520:end_scale=0.3", "-frames:v", "1", str(pic)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", "24", "-t", "2", "-i", str(pic),
                    "-vf", "crop=832:480:30+4*sin(n*2.3):18+4*cos(n*1.9)", "-pix_fmt", "yuv420p", "-crf", "4", str(src)], check=True)

    def jumps(out):
        fr = _frames(out)
        return sum(sum(abs(x - y) for x, y in zip(a, b)) / len(a) for a, b in zip(fr, fr[1:])) / len(fr)
    shaky, calm = (jumps(fit_clip(src, tmp_path / f"o{s}.mp4", seconds=1.8, steadied=s)) for s in (False, True))
    assert calm < .6 * shaky


def test_a_long_shot_is_planned_in_pieces():
    from app.story.motion import cut_times
    s = VideoSettings(832, 480, 81)
    sh = Shot(id="s", framing="medium", focus="upper left", duration_s=5.7, lines=[Line(speaker="Fox", text="Hello."), Line(speaker="Fox", text="Sing!")],
              cast=[CastEntry(name="Fox", action="on the grass, lower right"), CastEntry(name="Crow", action="on the branch, upper left")])
    voice = {"lines": [{"speaker": "Fox", "text": "Hello.", "start": 0, "seconds": 2}, {"speaker": "Fox", "text": "Sing!", "start": 2.5, "seconds": 2}]}
    a, b = A.pieces_of(sh, voice, s)
    assert abs(a.seconds + b.seconds - A.need_s(sh, False)) < .01 and [e.name for e in a.shot.cast] == ["Fox", "Crow"]
    assert [e.name for e in b.shot.cast] == ["Fox"] and b.crop == (.56, .85, .97)          # the closer view is on who speaks
    assert [e.name for e in A.pieces_of(sh, {}, s)[1].shot.cast] == ["Crow"]               # nobody recorded: who the camera is on
    sh.closer_on = "Crow"
    assert [e.name for e in A.pieces_of(sh, voice, s)[1].shot.cast] == ["Crow"]
    sh.duration_s = 8.2
    views = [[e.name for e in p.shot.cast] for p in A.pieces_of(sh, voice, s)]
    assert len(views) == 3 and views[1] != views[2]
    for need in (2.5, 3.0, 4.2, 5.8, 6.1, 7.2, 11):                                        # never longer than a clip (+ a short hold)
        t = [0, *cut_times(need, 2.875, .4, [], 4), need]
        assert all(y - x <= 2.875 + (.41 if y == need else .01) for x, y in zip(t, t[1:]))
    assert A.frames_for(2.0, s) == 61 and A.frames_for(9, s) == 81 and A.frames_for(.5, s) == 33
    assert all(f % 4 == 1 for f in (A.frames_for(x / 10, s) for x in range(5, 35)))


def test_eyes_and_mouths(tmp_path):
    from app.story.gaze import gaze_words, target
    _, cast = make_cast(tmp_path)
    by = {c.name: c for c in cast}
    fox, crow = CastEntry(name="Fox", action="on the grass, lower right"), CastEntry(name="Crow", action="on the branch, upper left", holding="cheese in her beak")
    sh = Shot(id="s", framing="wide", cast=[fox, crow], lines=[Line(speaker="Fox", text="Hi"), Line(speaker="Narrator", text="said the Fox.")])
    assert gaze_words(sh, fox) == "looks up at Crow" and gaze_words(sh, crow) == "looks down at Fox"
    p = motion_prompt(sh, by, "3d-cartoon")
    assert "mouth opens and closes as if talking" in p and "the beak stays closed around it" in p and p.count("blinks naturally") == 2
    sh.lines = sh.lines[1:]                                     # narrator only: mouths stay closed
    p = motion_prompt(sh, by, "3d-cartoon")
    assert "as if talking" not in p and "the mouth stays closed" in p
    near = motion_prompt(closer_shot(sh, ["Fox"]), by, "3d-cartoon", sh)
    assert "Crow" not in near and "looks up to the left" in near
    sh.visual_prompt = "The cheese falls through the air"       # both watch what falls
    assert target(sh, fox) == target(sh, crow) == "the cheese" and "eyes follow the cheese" in motion_prompt(sh, by, "3d-cartoon")
    fox.looking_at = "the Crow"
    assert gaze_words(sh, fox) == "looks up at Crow"
    crow.looking_at = "the camera"
    assert gaze_words(sh, crow) == "looks into the camera"
    fox.action = "on the grass, lower right, chews with closed eyes"
    q = motion_prompt(sh, by, "3d-cartoon")
    assert "mouth stays closed" not in q and q.count("blinks naturally,") == 1
    alone = Shot(id="s", cast=[CastEntry(name="Crow", action="on the grass")], visual_prompt="a wedge of cheese lies on the grass")
    assert gaze_words(alone, alone.cast[0]) == "looks at the wedge of cheese"


def test_prompts_are_encoded_apart_from_the_video_model():
    e, w = encode_workflow(["a", "b"]), wan_workflow("a", "i.png", s=A.LEVELS[2], seed=1)
    assert not any("Unet" in n["class_type"] for n in e.values())
    assert not any("CLIP" in n["class_type"] for n in w.values())
    keys = {n["inputs"]["key"] for n in e.values() if "key" in n["inputs"]}
    assert w["4"]["inputs"]["key"] in keys and w["5"]["inputs"]["key"] == prompt_key(NEGATIVE)


def test_motion_prompt_says_who_does_what(tmp_path):
    _, cast = make_cast(tmp_path)
    by = {c.name: c for c in cast}
    sh = Shot(id="s", framing="wide", motion="slow push in", lines=[Line(speaker="Fox", text="Hello!")],
              cast=[CastEntry(name="Fox", action="sits on the grass", expression="sly"), CastEntry(name="Crow", action="perched", holding="cheese in her beak")])
    p = motion_prompt(sh, by, "3d-cartoon")
    assert "Fox, the red fox: sits on the grass; mouth opens and closes as if talking" in p and "keeps holding small cheese in her beak, the beak stays closed around it" in p and "Static camera on a tripod" in p and "pushes" not in p
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
    assert fake.calls[0][1].startswith("960x544") and fake.calls[1][1].startswith("832x480")
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


def test_the_closer_piece_only_names_who_is_in_it(tmp_path):
    _, cast = make_cast(tmp_path)
    by = {c.name: c for c in cast}
    sh = Shot(id="s", framing="wide", focus="lower right", animation="The fox looks up. The crow sits still and blinks.",
              lines=[Line(speaker="Crow", text="Hi")],
              cast=[CastEntry(name="Fox", action="on the grass, lower right, looks up"), CastEntry(name="Crow", action="on the branch, upper left", holding="cheese")])
    near = motion_prompt(closer_shot(sh), by, "3d-cartoon")
    assert "Crow" not in near and "crow" not in near and "The fox looks up." in near
    assert "Crow" in motion_prompt(sh, by, "3d-cartoon")
    sh.focus = "centre"                                  # nobody placed there: everyone stays
    assert len(closer_shot(sh).cast) == 2
