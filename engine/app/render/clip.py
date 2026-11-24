"""Animated clip(s) (24 fps, short, smaller than the export) → a segment of exactly the length and frame the video needs.

Every frame is shown once, at the speed it was made: never slowed down, never backwards, no in-between frames. Each
piece loses its drifting end (trim); several pieces are cut together; if a piece is still too short its last frame is
held for a moment (the animate job keeps that under HOLD_S). Camera moves are added here, over clips filmed "from a
tripod": asked to move the camera, the video model shakes it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from app.render.ffmpeg import H264, duration, run
from app.render.motion import CLIP_FPS, OUTPUT_SIZES, camera_filter, clip_move

TRIM_S = 0.5      # animated clips drift at the end (faces, props): v1 measured in the last 8-12 of 81 frames
HOLD_S = 0.4      # a still last frame, at the very end of a shot
CLOSER_MOTION = "subtle push in"
MIN_S = 0.8       # never trim a piece shorter than this


def usable(have: float, trim: float) -> float:
    return max(min(have, MIN_S), have - trim)


def split_frames(frames: int, parts: int, cuts: list[float] | None, fps: int) -> list[int]:
    """Frames per piece; cuts = when pieces 2, 3… start (seconds), else equal shares."""
    at = [round(c * fps) for c in cuts or []]
    if len(at) != parts - 1 or any(not a < b for a, b in zip([0, *at], [*at, frames])):
        at = [round(frames * (i + 1) / parts) for i in range(parts - 1)]
    return [b - a for a, b in zip([0, *at], [*at, frames])]


def fit_clip(src: Path | list[Path], out: Path, *, seconds: float, aspect: str = "16:9", mode: Literal["cover", "blurpad"] = "cover",
             trim: float = 0.0, cuts: list[float] | None = None, motion: str = "", fps: int = CLIP_FPS, progress=None) -> Path:
    """motion: the shot's camera move, over its first piece (closer views: a slight push in)."""
    srcs = src if isinstance(src, list) else [src]
    w, h = OUTPUT_SIZES[aspect]
    frames = max(2, round(seconds * fps))
    graph = ""
    fit = f"scale={w}:{h}:force_original_aspect_ratio={'increase' if mode == 'cover' else 'decrease'}:flags=lanczos"
    fit += f",crop={w}:{h}" if mode == "cover" else ",scale=trunc(iw/2)*2:trunc(ih/2)*2"
    for i, (p, n) in enumerate(zip(srcs, split_frames(frames, len(srcs), cuts, fps))):
        use = usable(max(0.05, duration(p)), trim)
        keep = max(1, min(n, round(use * fps)))
        # fps= only matters for a clip that isn't at the project's rate; it repeats or drops frames, never blends
        graph += (f"[{i}:v]fps={fps},trim=end_frame={keep},setpts=N/{fps}/TB,tpad=stop_mode=clone:stop={n - keep},"
                  f"setpts=N/{fps}/TB,{fit},setsar=1,format=yuv444p,{camera_filter(clip_move(motion if i == 0 else CLOSER_MOTION), n)}[m{i}];")
    if len(srcs) > 1:
        graph += "".join(f"[m{i}]" for i in range(len(srcs))) + f"concat=n={len(srcs)}:v=1[vm];"
    else:
        graph += "[m0]null[vm];"
    if mode == "cover":
        graph += "[vm]unsharp=5:5:0.4:5:5:0,setsar=1,format=yuv420p[v]"
    else:
        graph += (f"[vm]split[a][b];[a]scale={w // 4}:{h // 4}:force_original_aspect_ratio=increase,crop={w // 4}:{h // 4},"
                  f"boxblur=10:2,eq=brightness=-0.06,scale={w}:{h}[bg];[b]unsharp=5:5:0.4:5:5:0[fg];"
                  f"[bg][fg]overlay=(W-w)/2:(H-h)/2:format=yuv444,setsar=1,format=yuv420p[v]")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part.mp4")
    run([*[a for p in srcs for a in ("-i", str(p))], "-filter_complex", graph, "-map", "[v]", "-frames:v", str(frames), *H264,
         "-r", str(fps), "-an", str(tmp)], duration_s=seconds, progress=progress, label="clip")
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
