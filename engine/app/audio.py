"""Small audio helpers (ffmpeg)."""
from __future__ import annotations

import shutil
from pathlib import Path

from app.render.ffmpeg import run


def pitch_shift(src: Path, out: Path, semitones: float) -> Path:
    """Shift pitch without changing speed (ffmpeg rubberband, formant-preserving)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    if abs(semitones) < 0.05:
        shutil.copy(src, out)
        return out
    ratio = 2 ** (semitones / 12)
    run(["-i", str(src), "-af", f"rubberband=pitch={ratio:.5f}:formant=preserved", str(out)], label="pitch")
    return out
