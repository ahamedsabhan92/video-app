"""Ken Burns motion: a still image → a short moving clip (slow zoom / pan).

The shot's free-text `motion` ("slow push in", "pan left", "gentle pull out"…) is mapped to a
camera move. Framing:
- "cover": crop the image to the output aspect (the image was made for this aspect).
- "blurpad": fit the whole image, over a blurred, enlarged copy of itself (used for the other
  aspect, e.g. a 16:9 image in a 9:16 Short, instead of a heavily upscaled crop).
zoompan runs on a 4x upscaled still so sub-pixel moves don't jitter.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.render.ffmpeg import H264, run

FPS = 30
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
                f"crop={W}:{H},setsar=1[still]")
    return (f"[0:v]split[a][b];"
            f"[a]scale={w // 4}:{h // 4}:force_original_aspect_ratio=increase,crop={w // 4}:{h // 4},"
            f"boxblur=10:2,eq=brightness=-0.06,scale={W}:{H}[bg];"
            f"[b]scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[still]")


def ken_burns(image: Path, out: Path, *, seconds: float, motion: str = "", aspect: str = "16:9",
              mode: Literal["cover", "blurpad"] = "cover", progress=None) -> Path:
    w, h = OUTPUT_SIZES[aspect]
    m = MOVES[parse_motion(motion)]
    frames = max(2, round(seconds * FPS))
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
             f";[still]zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={FPS},format=yuv420p[v]")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part.mp4")
    run(["-i", str(image), "-filter_complex", graph, "-map", "[v]",
         "-frames:v", str(frames), *H264, "-r", str(FPS), "-an", str(tmp)],
        duration_s=seconds, progress=progress, label="motion")
    tmp.replace(out)
    return out
