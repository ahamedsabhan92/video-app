"""Brand cards: opening ("Tiny Tales presents" + logo, then the title) and end card ("The End", logo, moral).

The background is the logo's own colour, so a logo with a solid background blends in. Only its artwork is used
(found by comparing with the corner colour), not the empty space around it.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.render.ffmpeg import FFMPEG, H264, probe, run
from app.render.motion import CLIP_FPS, OUTPUT_SIZES
from app.render.subtitles import _esc, _ts

FONT = "Fredoka"          # brand/fonts (OFL): rounded and bold, like the lettering of the logo
INK = "&H006B3022"        # &HAABBGGRR of dark navy #22306B
SCAN = 200
MORAL = 0.136             # size of the moral on the end card, of the short side
POP = r"\fscx70\fscy70\t(0,160,\fscx104\fscy104)\t(160,240,\fscx100\fscy100)"   # full colour at once (a fade-in looks grey)


@dataclass(frozen=True)
class Card:
    kind: str                  # "opening" | "end"
    seconds: float
    logo: Path | None
    background: str            # "#EAF2FF"
    top: str                   # "Tiny Tales presents" | "The End"
    bottom: str = ""           # the title | the moral

    def key(self) -> str:
        st = self.logo.stat() if self.logo else None
        return f"{self.kind}:{self.seconds}:{self.logo}:{st and (st.st_mtime_ns, st.st_size)}:{self.background}:{self.top}:{self.bottom}:c2"


def artwork(logo: Path) -> tuple[int, int, int, int, str | None]:
    """Where the artwork is in the file (x, y, w, h) and its solid background colour (None = transparent)."""
    v = probe(logo)["streams"][0]
    w, h = v["width"], v["height"]
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(logo), "-vf", f"scale={SCAN}:{SCAN}:flags=area", "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "rgba", "-"], capture_output=True, check=True).stdout
    corner = raw[:4]
    clear = corner[3] < 128
    xs, ys = [], []
    for y in range(SCAN):
        for x in range(SCAN):
            p = raw[4 * (y * SCAN + x):4 * (y * SCAN + x) + 4]
            if (p[3] > 24) if clear else (sum(abs(a - b) for a, b in zip(p[:3], corner[:3])) > 24):
                xs.append(x)
                ys.append(y)
    if not xs:
        return 0, 0, w, h, None
    x0, x1 = max(0, min(xs) - 2) / SCAN, min(SCAN, max(xs) + 3) / SCAN
    y0, y1 = max(0, min(ys) - 2) / SCAN, min(SCAN, max(ys) + 3) / SCAN
    return int(x0 * w), int(y0 * h), int((x1 - x0) * w), int((y1 - y0) * h), None if clear else "#%02X%02X%02X" % tuple(corner[:3])


def _lines(text: str, size: float, width: float) -> int:
    """About how many lines (Fredoka Bold: a letter is half as wide as the font size)."""
    n, at = 1, 0.0
    for word in text.split():
        ww = (len(word) + 1) * size * 0.52
        if at and at + ww > width:
            n, at = n + 1, 0.0
        at += ww
    return n


def _ass(card: Card, w: int, h: int) -> str:
    vertical = h > w
    base = min(w, h)
    side = round(w * 0.07)
    opening = card.kind == "opening"
    top, bottom = (0.085, 0.10) if opening else (0.13, MORAL)
    ty, by = ((0.22, 0.70) if vertical else (0.105, 0.87)) if opening else ((0.15, 0.76) if vertical else (0.10, 0.80))
    if not opening and card.bottom:              # a long moral: smaller, so it never runs into the logo
        room = h * (0.26 if vertical else 0.34)
        while bottom > 0.06 and _lines(card.bottom, base * bottom, w - 2 * side) * base * bottom * 1.25 > room:
            bottom *= 0.94
    style = lambda n, f: f"Style: {n},{FONT},{round(base * f)},{INK},{INK},{INK},&HFF000000,-1,0,0,0,100,100,0,0,1,0,0,5,{side},{side},0,1"  # noqa: E731
    lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}", "WrapStyle: 0", "ScaledBorderAndShadow: yes", "YCbCr Matrix: None", "",
             "[V4+ Styles]", "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, "
             "StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
             style("Top", top), style("Bottom", bottom), "", "[Events]",
             "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    end = _ts(card.seconds + 1)
    lines.append(f"Dialogue: 0,{_ts(0)},{end},Top,,0,0,0,,{{\\pos({w // 2},{round(h * ty)})}}{_esc(card.top)}")
    if card.bottom:
        at = 0.7 if opening else 1.0       # the title comes after "presents", the moral after "The End"
        lines.append(f"Dialogue: 0,{_ts(at)},{end},Bottom,,0,0,0,,{{\\pos({w // 2},{round(h * by)}){POP}}}{_esc(card.bottom)}")
    return "\n".join(lines) + "\n"


def _filter_path(p: Path) -> str:
    return re.sub(r"([\\:',])", r"\\\1", str(p))


def make_card(card: Card, out: Path, *, seconds: float, aspect: str, fonts: Path, fps: int = CLIP_FPS, progress=None) -> Path:
    """The card as a segment of exactly `seconds` (its own length + the transition into the next)."""
    w, h = OUTPUT_SIZES[aspect]
    out.parent.mkdir(parents=True, exist_ok=True)
    ass = out.with_suffix(".ass")
    ass.write_text(_ass(card, w, h), encoding="utf-8")
    args: list[str] = []
    bg = card.background
    graph = ""
    if card.logo:
        x, y, lw, lh, own = artwork(card.logo)
        bg = own or bg               # always the logo's own colour, or its edges would show
        opening = card.kind == "opening"
        room_w, room_h = (w * 0.84, h * 0.36) if h > w else (w * 0.7, h * (0.58 if opening else (0.44 if card.bottom else 0.66)))
        k = min(room_w / lw, room_h / lh)
        sw, sh = round(lw * k / 2) * 2, round(lh * k / 2) * 2
        cy = (0.46 if opening else 0.41) if h > w else (0.475 if opening else 0.40 if card.bottom else 0.55)
        args = ["-loop", "1", "-framerate", str(fps), "-t", f"{seconds:.3f}", "-i", str(card.logo)]
        fade = ",fade=t=in:st=0.1:d=0.5:alpha=1" if opening else ""
        graph = (f"[0:v]format=rgba,crop={lw}:{lh}:{x}:{y},scale={sw}:{sh}:flags=lanczos{fade}[l];"
                 f"color=c={bg.replace('#', '0x')}:s={w}x{h}:r={fps}:d={seconds:.3f},format=gbrp[b];"
                 f"[b][l]overlay={(w - sw) // 2}:{round(h * cy - sh / 2)}:format=gbrp:shortest=0[c];")
    else:
        graph = f"color=c={bg.replace('#', '0x')}:s={w}x{h}:r={fps}:d={seconds:.3f}[c];"
    graph += f"[c]subtitles=filename='{_filter_path(ass)}':fontsdir='{_filter_path(fonts)}',setsar=1,format=yuv420p[v]"
    tmp = out.with_suffix(".part.mp4")
    run([*args, "-filter_complex", graph, "-map", "[v]", "-frames:v", str(max(2, round(seconds * fps))), *H264, "-r", str(fps), "-an", str(tmp)],
        duration_s=seconds, progress=progress, label="card")
    tmp.replace(out)
    return out


@dataclass(frozen=True)
class Watermark:
    logo: Path
    corner: str = "bottom right"
    width: float = 0.10        # of the video width
    opacity: float = 0.70

    def place(self, w: int, h: int, blurpad: bool) -> tuple[int, int, int]:
        """(width, x, y) in pixels. In a vertical video: the corner of the sharp picture in the middle (blurred
        background), else above the buttons Shorts and TikTok draw at the bottom."""
        v = probe(self.logo)["streams"][0]
        ww = round(w * self.width / 2) * 2
        wh = round(ww * v["height"] / v["width"])
        m = round(w * 0.022)
        top, bottom = 0, h
        if h > w:
            pic = round(w * 9 / 16)
            top, bottom = ((h - pic) // 2, (h + pic) // 2) if blurpad else (round(h * 0.08), round(h * 0.78))
        x = m if "left" in self.corner else w - ww - m
        y = top + m if "top" in self.corner else bottom - wh - m
        return ww, x, y
