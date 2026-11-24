"""Ken Burns motion: a still image → a short moving clip (slow zoom / pan).

The shot's free-text `motion` ("slow push in", "pan left", "gentle pull out"…) is mapped to a
camera move. Framing:
- "cover": crop the image to the output aspect (the image was made for this aspect).
- "blurpad": fit the whole image, over a blurred, enlarged copy of itself (used for the other
  aspect, e.g. a 16:9 image in a 9:16 Short, instead of a heavily upscaled crop).
zoompan moves in whole source pixels, so it runs on a 4x upscaled still, in 4:4:4: in 4:2:0 it only moves in
two-pixel steps, which showed as jitter in slow moves.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.render.ffmpeg import H264, run

FPS = 30          # stills with Ken Burns
CLIP_FPS = 24     # animated projects: the clips' own rate, for every frame of the video
UPSCALE = 4

OUTPUT_SIZES = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350)}


@dataclass(frozen=True)
class Move:
    """Zoom from z0→z1 while the window centre moves from (x0,y0)→(x1,y1); 0.5 = centre."""
    z0: float
    z1: float
    x0: float = 0.5
    x1: float = 0.5
    y0: float = 0.5
    y1: float = 0.5


MOVES = {
    "push_in": Move(1.0, 1.15),
    "pull_out": Move(1.15, 1.0),
    "pan_right": Move(1.12, 1.12, 0.38, 0.62),
    "pan_left": Move(1.12, 1.12, 0.62, 0.38),
    "pan_up": Move(1.12, 1.12, 0.5, 0.5, 0.6, 0.4),
    "pan_down": Move(1.12, 1.12, 0.5, 0.5, 0.4, 0.6),
    "subtle_push_in": Move(1.0, 1.08),
    "hold": Move(1.04, 1.06),  # nearly still, but never frozen
}


# the same moves over an animated clip: the characters move already, and the clip is a small picture
CLIP_MOVES = {
    "push_in": Move(1.0, 1.06), "subtle_push_in": Move(1.0, 1.04), "pull_out": Move(1.06, 1.0), "hold": Move(1.0, 1.0),
    "pan_right": Move(1.06, 1.06, 0.2, 0.8), "pan_left": Move(1.06, 1.06, 0.8, 0.2),
    "pan_up": Move(1.06, 1.06, 0.5, 0.5, 0.8, 0.2), "pan_down": Move(1.06, 1.06, 0.5, 0.5, 0.2, 0.8),
}


def clip_move(text: str) -> Move:
    key = parse_motion(text) if text.strip() else "hold"
    if key == "subtle_push_in" and not re.search(r"push|zoom|closer|subtle|gentle|slight", text, re.I):
        key = "hold"
    return CLIP_MOVES[key]


def camera_filter(m: Move, frames: int) -> str:
    """The move as an ffmpeg filter for a moving picture, placed between pixels (whole-pixel steps shake)."""
    if m == Move(1.0, 1.0):
        return "null"
    n = max(1, frames - 1)
    p = f"min(on/{n}\\,1)"
    ease = f"({p}*{p}*(3-2*{p}))"
    k = f"(1/({m.z0}+({m.z1 - m.z0:.4f})*{ease}))"                       # how much of the picture is seen
    x, y = (f"({a}+({b - a:.4f})*{ease})*(1-{k})" for a, b in ((m.x0, m.x1), (m.y0, m.y1)))
    left, right, top, bottom = f"W*({x})", f"W*({x}+{k})", f"H*({y})", f"H*({y}+{k})"
    return (f"perspective=x0={left}:y0={top}:x1={right}:y1={top}:x2={left}:y2={bottom}:x3={right}:y3={bottom}:"
            f"interpolation=cubic:eval=frame")


def parse_motion(text: str) -> str:
    t = text.lower()
    rules = [
        (r"pull (out|back)|zoom(s|ing)? out|dolly out", "pull_out"),
        (r"pan(s|ning)? (to the )?right|track(s|ing)? right|moves? right", "pan_right"),
        (r"pan(s|ning)? (to the )?left|track(s|ing)? left|moves? left", "pan_left"),
        (r"(tilt|pan|drift)(s|ing)? up|rises?", "pan_up"),
        (r"(tilt|pan|drift)(s|ing)? down", "pan_down"),
        (r"subtle|gentle|slight", "subtle_push_in"),
        (r"push(es|ing)? in|zoom(s|ing)? in|dolly in|closer", "push_in"),
        (r"steady|still|static|hold", "hold"),
    ]
    for pattern, key in rules:
        if re.search(pattern, t):
            return key
    return "subtle_push_in"  # subject moves ("she waves", "follows her") → gentle push


def _framing_filter(w: int, h: int, mode: Literal["cover", "blurpad"]) -> str:
    """Filter graph from input [0:v] to a still of exactly UPSCALE*(w x h), labelled [still]."""
    W, H = w * UPSCALE, h * UPSCALE
    if mode == "cover":
        return (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={W}:{H},setsar=1,format=yuv444p[still]")
    return (f"[0:v]split[a][b];"
            f"[a]scale={w // 4}:{h // 4}:force_original_aspect_ratio=increase,crop={w // 4}:{h // 4},"
            f"boxblur=10:2,eq=brightness=-0.06,scale={W}:{H}[bg];"
            f"[b]scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2:format=yuv444,setsar=1,format=yuv444p[still]")


def ken_burns(image: Path, out: Path, *, seconds: float, motion: str = "", aspect: str = "16:9",
              mode: Literal["cover", "blurpad"] = "cover", fps: int = FPS, progress=None) -> Path:
    w, h = OUTPUT_SIZES[aspect]
    m = MOVES[parse_motion(motion)]
    frames = max(2, round(seconds * fps))
    n = frames - 1
    # Linear interpolation over output frame number `on`; smoothstep for a softer start/end.
    ease = f"(3*pow(on/{n},2)-2*pow(on/{n},3))"
    z = f"{m.z0}+({m.z1 - m.z0})*{ease}"
    cx = f"({m.x0}+({m.x1 - m.x0})*{ease})"
    cy = f"({m.y0}+({m.y1 - m.y0})*{ease})"
    # Window top-left so its centre sits at (cx, cy), clamped inside the image.
    x = f"max(0,min(iw-iw/zoom,{cx}*iw-iw/zoom/2))"
    y = f"max(0,min(ih-ih/zoom,{cy}*ih-ih/zoom/2))"
    graph = (_framing_filter(w, h, mode) +
             f";[still]zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={fps},format=yuv420p[v]")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part.mp4")
    run(["-i", str(image), "-filter_complex", graph, "-map", "[v]",
         "-frames:v", str(frames), *H264, "-r", str(fps), "-an", str(tmp)],
        duration_s=seconds, progress=progress, label="motion")
    tmp.replace(out)
    return out
