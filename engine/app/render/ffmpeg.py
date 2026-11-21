"""Small helpers around the ffmpeg/ffprobe CLIs (Homebrew ffmpeg-full)."""
from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

FFMPEG, FFPROBE = "ffmpeg", "ffprobe"
H264 = ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"]


class FFmpegError(RuntimeError):
    pass


def run(args: list[str], *, duration_s: float | None = None,
        progress: Callable[[float | None, str], None] | None = None, label: str = "ffmpeg") -> None:
    """Run ffmpeg, report progress from `-progress pipe:1` (needs duration_s), raise with stderr tail."""
    cmd = [FFMPEG, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-progress", "pipe:1", *args]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert p.stdout is not None
        for line in p.stdout:
            if progress and duration_s and line.startswith("out_time_us="):
                try:
                    t = int(line.split("=", 1)[1]) / 1e6
                except ValueError:
                    continue
                progress(min(0.99, t / duration_s), label)  # may raise JobCancelled
    except BaseException:
        p.kill()
        p.wait()
        raise
    err = p.stderr.read() if p.stderr else ""
    if p.wait() != 0:
        raise FFmpegError(f"{label} failed: {err.strip()[-1200:]}")


def probe(path: Path) -> dict:
    out = subprocess.run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                          str(path)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def duration(path: Path) -> float:
    return float(probe(path)["format"]["duration"])


def loudness(path: Path) -> dict[str, float]:
    """Integrated loudness (LUFS), true peak (dBTP) and LRA via ffmpeg's ebur128 filter."""
    p = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-i", str(path), "-filter_complex",
                        "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True)
    summary = p.stderr[p.stderr.rfind("Summary:"):]
    def grab(key: str) -> float:
        m = re.search(rf"{key}:\s+(-?[\d.]+|-inf)", summary)
        return float(m.group(1)) if m and m.group(1) != "-inf" else float("-inf")
    return {"lufs": grab("I"), "true_peak": grab("Peak"), "lra": grab("LRA")}
