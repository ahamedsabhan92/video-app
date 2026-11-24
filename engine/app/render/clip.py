"""Animated clip (24 fps, short, smaller than the export) → a segment of exactly the length and frame the video needs.

Too long: trimmed. A bit short: played slower with motion-interpolated in-between frames (up to MAX_SLOW).
Still short: the last frame is held. (The animate job makes enough footage, so holding is the exception.)
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from app.render.ffmpeg import H264, duration, run
from app.render.motion import FPS, OUTPUT_SIZES

MAX_SLOW = 1.75


def fit_clip(src: Path, out: Path, *, seconds: float, aspect: str = "16:9", mode: Literal["cover", "blurpad"] = "cover",
             progress=None) -> Path:
    w, h = OUTPUT_SIZES[aspect]
    have = max(0.05, duration(src))
    slow = min(MAX_SLOW, max(1.0, seconds / have))
    vf = f"setpts={slow:.5f}*PTS,minterpolate=fps={FPS}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1"
    vf += f",tpad=stop_mode=clone:stop_duration={max(0.0, seconds - have * slow) + 0.5:.3f}"
    if mode == "cover":
        graph = (f"[0:v]{vf},scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h},"
                 f"unsharp=5:5:0.4:5:5:0,setsar=1,format=yuv420p[v]")
    else:
        graph = (f"[0:v]{vf},split[a][b];[a]scale={w // 4}:{h // 4}:force_original_aspect_ratio=increase,crop={w // 4}:{h // 4},"
                 f"boxblur=10:2,eq=brightness=-0.06,scale={w}:{h}[bg];[b]scale={w}:{h}:force_original_aspect_ratio=decrease:"
                 f"flags=lanczos,unsharp=5:5:0.4:5:5:0[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p[v]")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part.mp4")
    run(["-i", str(src), "-filter_complex", graph, "-map", "[v]", "-frames:v", str(max(2, round(seconds * FPS))), *H264,
         "-r", str(FPS), "-an", str(tmp)], duration_s=seconds, progress=progress, label="clip")
    tmp.replace(out)
    return out


def last_frame(src: Path, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    run(["-sseof", "-0.5", "-i", str(src), "-update", "1", "-q:v", "1", str(out)], label="last frame")
    return out


def join(parts: list[Path], out: Path) -> Path:
    """Consecutive pieces of one shot; a later piece starts with the former's last frame, which is dropped."""
    if len(parts) == 1:
        import shutil
        shutil.copy2(parts[0], out)
        return out
    args: list[str] = []
    for p in parts:
        args += ["-i", str(p)]
    chain = "".join(f"[{i}:v]{'trim=start_frame=1,' if i else ''}setpts=PTS-STARTPTS[p{i}];" for i in range(len(parts)))
    chain += "".join(f"[p{i}]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=1[v]"
    run([*args, "-filter_complex", chain, "-map", "[v]", *H264, "-crf", "14", "-an", str(out)], label="join")
    return out
