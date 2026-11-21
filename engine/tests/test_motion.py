import subprocess

import pytest

from app.render.ffmpeg import probe
from app.render.motion import parse_motion, ken_burns
from app.render.placeholder import make_placeholder


@pytest.mark.parametrize("text, key", [
    ("slow push in", "push_in"), ("camera slowly zooms in", "push_in"), ("gentle pull out", "pull_out"),
    ("pull back", "pull_out"), ("slow pan right", "pan_right"), ("pan left", "pan_left"),
    ("subtle push in", "subtle_push_in"), ("camera stays steady", "hold"), ("tilt up", "pan_up"),
    ("she waves", "subtle_push_in"), ("", "subtle_push_in"),
])
def test_parse_motion(text, key):
    assert parse_motion(text) == key


def _video(path):
    return next(s for s in probe(path)["streams"] if s["codec_type"] == "video")


def _frame_bytes(path, n):
    return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"select=eq(n\\,{n}),scale=64:36",
                           "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                          capture_output=True, check=True).stdout


@pytest.mark.parametrize("aspect, mode, size", [("16:9", "cover", (1920, 1080)), ("9:16", "blurpad", (1080, 1920))])
def test_ken_burns_output_format_and_motion(tmp_path, aspect, mode, size):
    img = make_placeholder(tmp_path / "s01.png", "s01")
    out = ken_burns(img, tmp_path / "clip.mp4", seconds=2.0, motion="slow push in", aspect=aspect, mode=mode)
    v = _video(out)
    assert (v["width"], v["height"]) == size
    assert v["codec_name"] == "h264" and v["pix_fmt"] == "yuv420p" and int(v["nb_frames"]) == 60
    assert _frame_bytes(out, 0) != _frame_bytes(out, 59)  # the camera actually moved
