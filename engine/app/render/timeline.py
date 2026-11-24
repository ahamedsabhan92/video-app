"""Timeline: where every shot, line of narration, caption and learning word sits in time.

Each segment's clip is (visible length + XFADE) except the last, so with crossfades segment k
starts exactly at the sum of the visible lengths before it (no drift).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re

from app.script.schema import Script

XFADE_S = 0.4      # between stills
CUT_FRAMES = 1     # between two animated shots: a clean cut (a one-frame transition has no blended frame).
                   # A dissolve of two moving pictures looks like a double exposure, even for one frame.
INTRO_S = 2.5
OPENING_S = 3.0    # brand cards
END_S = 5.0
END_FADE_S = 0.9
LEAD_IN_S = 0.25   # narration starts this long after its shot (matches pipeline.narration)


@dataclass
class Segment:
    kind: str                 # "intro" | "opening" | "shot" | "end"
    id: str
    image: Path
    start: float
    visible: float            # seconds on screen (excluding the overlap into the next one)
    clip_len: float           # rendered clip length (visible + XFADE, except the last)
    motion: str = ""
    narration: str = ""
    voice: Path | None = None
    voice_s: float = 0.0
    clip: Path | None = None  # animated clip; None = Ken Burns on the picture
    parts: list[Path] = field(default_factory=list)   # its pieces, when it has several (a camera cut inside the shot)
    trim: float = 0.0         # seconds cut off each piece's end, where animated clips drift
    cuts: list[float] = field(default_factory=list)   # when its pieces 2, 3… start
    xfade: float = 0.0        # length of the transition into the next segment


@dataclass
class TextCue:
    kind: str                 # "caption" | "learning" | "title"
    text: str
    start: float
    end: float


@dataclass
class Timeline:
    segments: list[Segment]
    cues: list[TextCue] = field(default_factory=list)
    title: str = ""
    fps: int = 30             # 24 (the clips' own rate) as soon as one shot is animated

    @property
    def duration(self) -> float:
        last = self.segments[-1]
        return round(last.start + last.visible, 6)


def set_transitions(segs: list[Segment], fps: int = 30) -> None:
    """Transition lengths, and how long each segment's file must be. Call again after assigning clips."""
    for a, b in zip(segs, [*segs[1:], None]):
        a.xfade = 0.0 if b is None else END_FADE_S if b.kind == "end" else CUT_FRAMES / fps if a.clip and b.clip else XFADE_S
        a.xfade = round(a.xfade * fps) / fps
        a.clip_len = round(a.visible + a.xfade, 6)


def build_timeline(script: Script, project_dir: Path, images: dict, voice: dict, *,
                   intro: bool = True, kids: bool = True, dialogue_captions: bool = True,
                   opening: bool = False, end: bool = False, fps: int = 30) -> Timeline:
    """fps: shots start and end on whole frames, so cuts and clips stay exact. opening / end: room for the brand cards (the opening card replaces the plain title intro)."""
    segs: list[Segment] = []
    t = 0.0

    def snap(x: float) -> float:
        return round(round(x * fps) / fps, 6)

    shots = script.shots
    if opening:
        segs.append(Segment("opening", "opening", project_dir / images[shots[0].id]["path"], 0.0, OPENING_S, 0.0))
        t = OPENING_S
        intro = False
    if intro:
        segs.append(Segment("intro", "intro", project_dir / images[shots[0].id]["path"], 0.0, INTRO_S, 0.0,
                            motion="gentle pull out"))
        t = INTRO_S
    for shot in shots:
        meta = voice.get(shot.id)
        segs.append(Segment("shot", shot.id, project_dir / images[shot.id]["path"], snap(t), round(snap(t + shot.duration_s) - snap(t), 6),
                            0.0, motion=shot.motion, narration=" ".join(l.text for l in shot.spoken()),
                            voice=project_dir / meta["path"] if meta else None,
                            voice_s=meta["seconds"] if meta else 0.0))
        t += shot.duration_s
    if end:
        segs.append(Segment("end", "end", project_dir / images[shots[-1].id]["path"], snap(t), END_S, 0.0))
    set_transitions(segs, fps)

    tl = Timeline(segs, title=script.title, fps=fps)
    if intro:
        tl.cues.append(TextCue("title", script.title, 0.2, INTRO_S - 0.1))
    by_id = {s.id: s for s in segs}
    for shot in shots:
        seg = by_id[shot.id]
        if shot.lines:
            tl.cues += _line_cues(shot, seg, (voice.get(shot.id) or {}).get("lines") or [], dialogue_captions)
        elif shot.narration:
            end = seg.start + (LEAD_IN_S + seg.voice_s + 0.25 if seg.voice_s else seg.visible)
            tl.cues += _split_cues("caption", shot.narration, seg.start + LEAD_IN_S * 0.5, min(end, seg.start + seg.visible))
    if kids:
        for scene in script.scenes:
            if scene.learning_word and scene.shots:
                first, last = by_id[scene.shots[0].id], by_id[scene.shots[-1].id]
                tl.cues.append(TextCue("learning", scene.learning_word, first.start + 0.15,
                                       last.start + last.visible - 0.1))
    return tl


def _line_cues(shot, seg: Segment, voiced: list[dict], dialogue_captions: bool) -> list[TextCue]:
    """One caption per spoken line, timed to its voice. Characters' lines carry their name ("Fox: …")
    and can be switched off per project; the narrator's captions always show."""
    lines = shot.spoken()
    if len(voiced) == len(lines):
        times = [(v["start"], v["seconds"]) for v in voiced]
    else:                                  # not spoken yet: share the shot by word count
        total = sum(len(l.text.split()) for l in lines) or 1
        span, t, times = max(0.5, seg.visible - 2 * LEAD_IN_S), 0.0, []
        for l in lines:
            d = span * len(l.text.split()) / total
            times.append((t, d))
            t += d
    out = []
    for k, (l, (start, secs)) in enumerate(zip(lines, times)):
        told = l.speaker == "Narrator"
        if not told and not dialogue_captions:
            continue
        a = seg.start + LEAD_IN_S + start - (LEAD_IN_S * 0.5 if k == 0 else 0)
        end = seg.start + LEAD_IN_S + start + secs + 0.25
        if k + 1 < len(times):
            end = min(end, seg.start + LEAD_IN_S + times[k + 1][0])
        out += _split_cues("caption" if told else "dialogue", l.text if told else f"{l.speaker}: {l.text}", a,
                           min(end, seg.start + seg.visible))
    return out


CAPTION_CHARS = 66      # about two lines in 16:9; longer texts are shown one sentence after the other


def split_caption(text: str, limit: int = CAPTION_CHARS) -> list[str]:
    if len(text) <= limit:
        return [text]
    for pattern in (r"(?<=[.!?…])\s+", r"(?<=[,;:])\s+"):
        bits = re.split(pattern, text)
        if len(bits) > 1:
            out: list[str] = []
            for b in bits:
                if out and len(out[-1]) + 1 + len(b) <= limit:
                    out[-1] += " " + b
                else:
                    out.append(b)
            return [y for x in out for y in (split_caption(x, limit) if len(x) > limit and len(out) > 1 else [x])]
    return [text]


def _split_cues(kind: str, text: str, start: float, end: float) -> list[TextCue]:
    """One cue, or several sharing the time by length."""
    bits = split_caption(text)
    total, t, out = sum(len(b) for b in bits), start, []
    for b in bits:
        d = (end - start) * len(b) / total
        out.append(TextCue(kind, b, t, t + d))
        t += d
    return out
