"""ASS subtitle file for captions, learning words and the title card (burned in by libass)."""
from __future__ import annotations

from app.render.timeline import Timeline

FONT = "Arial Rounded MT Bold"   # ships with macOS; friendly rounded look


def _ts(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", "\\N")


def build_ass(tl: Timeline, width: int, height: int, brand: str = "", keep_clear: int = 0) -> str:
    """keep_clear: pixels at both sides captions stay out of (the corner watermark)."""
    vertical = height > width
    base = width if vertical else height          # scale text to the short side
    cap = round(base * (0.0744 if vertical else 0.0768))        # 20% bigger than in the first videos
    learn = round(base * (0.085 if vertical else 0.075))
    title = round(base * (0.10 if vertical else 0.095))
    small = round(base * 0.03)
    cap_margin = round(height * (0.20 if vertical else 0.06))   # Shorts UI covers the bottom
    learn_margin = round(height * (0.10 if vertical else 0.05))
    side = max(round(width * 0.06), keep_clear)
    outline = max(2, round(base * 0.006))
    # ASS colours are &HAABBGGRR
    styles = [
        f"Style: Caption,{FONT},{cap},&H00FFFFFF,&H00FFFFFF,&H00202020,&H64000000,0,0,0,0,100,100,0,0,1,"
        f"{outline + 2},2,2,{side},{side},{cap_margin},1",
        # dialogue: like a caption, warm yellow so it reads as somebody speaking
        f"Style: Dialogue,{FONT},{cap},&H0096E6FF,&H0096E6FF,&H00202020,&H64000000,0,0,0,0,100,100,0,0,1,"
        f"{outline + 2},2,2,{side},{side},{cap_margin},1",
        f"Style: Learning,{FONT},{learn},&H0000D7FF,&H0000D7FF,&H00402010,&H64000000,0,0,0,0,100,100,2,0,1,"
        f"{outline + 3},3,8,{side},{side},{learn_margin},1",
        f"Style: Title,{FONT},{title},&H00FFFFFF,&H00FFFFFF,&H00402010,&H80000000,0,0,0,0,100,100,1,0,1,"
        f"{outline + 4},4,5,{side},{side},0,1",
        f"Style: Brand,{FONT},{small},&H40FFFFFF,&H40FFFFFF,&H60000000,&H00000000,0,0,0,0,100,100,0,0,1,"
        f"2,0,9,{side // 2},{side // 2},{side // 2},1",
    ]
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        *styles, "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    style = {"caption": "Caption", "dialogue": "Dialogue", "learning": "Learning", "title": "Title"}
    fade = {"caption": r"{\fad(120,120)}", "dialogue": r"{\fad(120,120)}", "learning": r"{\fad(200,200)}", "title": r"{\fad(300,300)}"}
    for c in tl.cues:
        lines.append(f"Dialogue: 0,{_ts(c.start)},{_ts(c.end)},{style[c.kind]},,0,0,0,,{fade[c.kind]}{_esc(c.text)}")
    if brand:
        lines.append(f"Dialogue: 1,{_ts(0)},{_ts(tl.duration)},Brand,,0,0,0,,{_esc(brand)}")
    return "\n".join(lines) + "\n"
