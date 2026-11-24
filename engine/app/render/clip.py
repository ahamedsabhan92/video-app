"""Animated clip(s) (24 fps, short, smaller than the export) → a segment of exactly the length and frame the video needs.

Each piece loses its drifting end (trim). Too long: cut. A bit short: played slower with motion-interpolated
in-between frames (up to MAX_SLOW); what is then still missing plays backwards from the end (a living picture instead
of a frozen one; the animate job keeps that under BACK_S). Several pieces are cut together.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from app.render.ffmpeg import H264, duration, run
from app.render.motion import FPS, OUTPUT_SIZES

MAX_SLOW = 1.75
TRIM_S = 0.5      # animated clips drift at the end (faces, props): v1 measured in the last 8-12 of 81 frames
BACK_S = 0.7
MIN_S = 0.8       # never trim a piece shorter than this


def usable(have: float, trim: float) -> float:
    return max(min(have, MIN_S), have - trim)


def _motion(i: int, src: Path, seconds: float, trim: float, pulse: int) -> str:
    """Filters for piece i: [i:v] → [m{i}], at least `seconds` long at FPS."""
    use = usable(max(0.05, duration(src)), trim)
    slow = min(MAX_SLOW, max(1.0, seconds / use))
    pre = f"[{i}:v]trim=duration={use:.4f},setpts=PTS-STARTPTS"
    if pulse:     # leave out the bright frames at the start of every old decode chunk; they are interpolated anew
        pre += f",select='lt(n,{pulse})+gte(mod(n,{pulse}),4)'"
    interp = f"setpts={slow:.5f}*PTS,minterpolate=fps={FPS}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1"
    if seconds > use * slow + 0.05:
        pre += (f",split[f{i}][r{i}];[r{i}]reverse,setpts=PTS-STARTPTS[b{i}];"
                f"[f{i}][b{i}]concat=n=2:v=1")
        use *= 2
    n = max(1, round(seconds * FPS))
    return (f"{pre},{interp},setpts=PTS-STARTPTS,fps={FPS},tpad=stop_mode=clone:stop_duration={max(0.0, seconds - use * slow) + 1:.3f},"
            f"trim=end_frame={n},setpts=N/{FPS}/TB[m{i}]")


def fit_clip(src: Path | list[Path], out: Path, *, seconds: float, aspect: str = "16:9", mode: Literal["cover", "blurpad"] = "cover",
             trim: float = 0.0, pulse: int = 0, progress=None) -> Path:
    srcs = src if isinstance(src, list) else [src]
    w, h = OUTPUT_SIZES[aspect]
    frames = max(2, round(seconds * FPS))
    each = [frames // len(srcs)] * len(srcs)
    each[-1] += frames - sum(each)
    graph = ";".join(_motion(i, p, n / FPS, trim, pulse) for i, (p, n) in enumerate(zip(srcs, each))) + ";"
    if len(srcs) > 1:
        graph += "".join(f"[m{i}]" for i in range(len(srcs))) + f"concat=n={len(srcs)}:v=1[vm];"
    else:
        graph += "[m0]null[vm];"
    if mode == "cover":
        graph += (f"[vm]scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h},"
                  f"unsharp=5:5:0.4:5:5:0,setsar=1,format=yuv420p[v]")
    else:
        graph += (f"[vm]split[a][b];[a]scale={w // 4}:{h // 4}:force_original_aspect_ratio=increase,crop={w // 4}:{h // 4},"
                  f"boxblur=10:2,eq=brightness=-0.06,scale={w}:{h}[bg];[b]scale={w}:{h}:force_original_aspect_ratio=decrease:"
                  f"flags=lanczos,unsharp=5:5:0.4:5:5:0[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p[v]")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part.mp4")
    run([*[a for p in srcs for a in ("-i", str(p))], "-filter_complex", graph, "-map", "[v]", "-frames:v", str(frames), *H264,
         "-r", str(FPS), "-an", str(tmp)], duration_s=seconds, progress=progress, label="clip")
    tmp.replace(out)
    return out


def crop_picture(src: Path, out: Path, fraction: float, fx: float, fy: float) -> Path:
    """A closer view of the same picture (centre at fx, fy; kept inside), for the second piece of a long shot."""
    out.parent.mkdir(parents=True, exist_ok=True)
    run(["-i", str(src), "-vf", f"crop=trunc(iw*{fraction}/2)*2:trunc(ih*{fraction}/2)*2:(iw-ow)*{fx}:(ih-oh)*{fy}", "-frames:v", "1", str(out)],
        label="closer view")
    return out


def join(parts: list[Path], out: Path) -> Path:
    """The pieces one after the other, for the preview in the storyboard."""
    if len(parts) == 1:
        import shutil
        shutil.copy2(parts[0], out)
        return out
    args = [a for p in parts for a in ("-i", str(p))]
    chain = "".join(f"[{i}:v]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=1[v]"
    run([*args, "-filter_complex", chain, "-map", "[v]", *H264, "-crf", "14", "-an", str(out)], label="join")
    return out
