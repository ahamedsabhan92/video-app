"""Does steadying a clip help? The background is measured with and without it, at the picture's edges: in a close-up
the stabiliser follows the head, and the background shakes instead."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from app.render.ffmpeg import FFMPEG

W, H = 960, 540
# left, right, upper left, upper right (y0, y1, x0, x1): where characters rarely are
EDGES = ((54, 360, 0, 160), (54, 360, 800, 960), (0, 150, 0, 260), (0, 150, 700, 960))


def _shift(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    """How far b is moved against a, in pixels (phase correlation, to a fraction of a pixel)."""
    h, w = a.shape
    win = np.outer(np.hanning(h), np.hanning(w))
    fa, fb = (np.fft.rfft2((x - x.mean()) * win) for x in (a, b))
    r = fb * np.conj(fa)
    c = np.fft.irfft2(r / (np.abs(r) + 1e-6 * np.abs(r).max() + 1e-12), s=a.shape)
    y, x = np.unravel_index(c.argmax(), c.shape)

    def part(lo, mid, hi):
        d = lo - 2 * mid + hi
        return 0.0 if abs(d) < 1e-9 else 0.5 * (lo - hi) / d
    dx = x + part(c[y, x - 1], c[y, x], c[y, (x + 1) % w])
    dy = y + part(c[y - 1, x], c[y, x], c[(y + 1) % h, x])
    return dx - w if dx > w / 2 else dx, dy - h if dy > h / 2 else dy


def shake(src: Path, vf: str, seconds: float) -> float:
    """Trembling of the background: its movement per frame without the smooth part, in pixels of a 1920 wide picture."""
    raw = subprocess.run([FFMPEG, "-v", "error", "-nostdin", "-i", str(src), "-vf", f"{vf},trim=duration={seconds:.3f},scale={W}:{H}:flags=area,format=gray",
                          "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    f = np.frombuffer(raw, np.uint8).reshape(-1, H, W).astype(np.float32)
    if len(f) < 8:
        return 0.0
    out = []
    for y0, y1, x0, x1 in EDGES:
        e = f[:, y0:y1, x0:x1]
        if e[0].std() < 8:            # plain sky: nothing to measure
            continue
        s = np.array([_shift(a, b) for a, b in zip(e, e[1:])])
        smooth = np.stack([np.convolve(np.pad(s[:, j], 2, mode="edge"), np.ones(5) / 5, "valid") for j in (0, 1)], 1)
        out.append(float(np.hypot(*(s - smooth).T).mean()) * 1920 / W)
    return float(np.median(out)) if out else 0.0
