"""Placeholder shot images (grid + label) for testing motion/render before real images exist."""
from __future__ import annotations

from pathlib import Path

from app.render.ffmpeg import run

COLORS = ["0x3b82f6", "0x10b981", "0xf59e0b", "0xef4444", "0x8b5cf6", "0x06b6d4", "0xec4899", "0x84cc16"]


def make_placeholder(out: Path, label: str, *, width: int = 1360, height: int = 768, index: int = 0) -> Path:
    color = COLORS[index % len(COLORS)]
    fs = height // 6
    vf = (f"drawgrid=w={width // 8}:h={height // 8}:t=2:c=white@0.35,"
          f"drawbox=x={width // 2 - 6}:y={height // 2 - 6}:w=12:h=12:c=white:t=fill,"
          f"drawtext=text='{label}':fontcolor=white:fontsize={fs}:x=(w-tw)/2:y=(h-th)/2-{fs}:"
          f"borderw=4:bordercolor=black@0.6")
    out.parent.mkdir(parents=True, exist_ok=True)
    run(["-f", "lavfi", "-i", f"color=c={color}:s={width}x{height}", "-vf", vf, "-frames:v", "1", str(out)],
        label="placeholder")
    return out
