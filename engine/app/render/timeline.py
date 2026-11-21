"""Timeline: where every shot, line of narration, caption and learning word sits in time.

Each segment's clip is (visible length + XFADE) except the last, so with crossfades segment k
starts exactly at the sum of the visible lengths before it (no drift).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.script.schema import Script

XFADE_S = 0.4
INTRO_S = 2.5
LEAD_IN_S = 0.25   # narration starts this long after its shot (matches pipeline.narration)


@dataclass
class Segment:
    kind: str                 # "intro" | "shot"
    id: str
    image: Path
    start: float
    visible: float            # seconds on screen (excluding the overlap into the next one)
    clip_len: float           # rendered clip length (visible + XFADE, except the last)
    motion: str = ""
    narration: str = ""
    voice: Path | None = None
    voice_s: float = 0.0


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

    @property
    def duration(self) -> float:
        last = self.segments[-1]
        return round(last.start + last.visible, 3)


def build_timeline(script: Script, project_dir: Path, images: dict, voice: dict, *,
                   intro: bool = True, kids: bool = True) -> Timeline:
    segs: list[Segment] = []
    t = 0.0
    shots = script.shots
    if intro:
        segs.append(Segment("intro", "intro", project_dir / images[shots[0].id]["path"], 0.0, INTRO_S, 0.0,
                            motion="gentle pull out"))
        t = INTRO_S
    for shot in shots:
        meta = voice.get(shot.id)
        segs.append(Segment("shot", shot.id, project_dir / images[shot.id]["path"], round(t, 3), shot.duration_s,
                            0.0, motion=shot.motion, narration=shot.narration,
                            voice=project_dir / meta["path"] if meta else None,
                            voice_s=meta["seconds"] if meta else 0.0))
        t += shot.duration_s
    for i, s in enumerate(segs):
        s.clip_len = round(s.visible + (XFADE_S if i < len(segs) - 1 else 0.0), 3)

    tl = Timeline(segs, title=script.title)
    if intro:
        tl.cues.append(TextCue("title", script.title, 0.2, INTRO_S - 0.1))
    by_id = {s.id: s for s in segs}
    for shot in shots:
        seg = by_id[shot.id]
        if shot.narration:
            end = seg.start + (LEAD_IN_S + seg.voice_s + 0.25 if seg.voice_s else seg.visible)
            tl.cues.append(TextCue("caption", shot.narration, seg.start + LEAD_IN_S * 0.5,
                                   min(end, seg.start + seg.visible)))
    if kids:
        for scene in script.scenes:
            if scene.learning_word and scene.shots:
                first, last = by_id[scene.shots[0].id], by_id[scene.shots[-1].id]
                tl.cues.append(TextCue("learning", scene.learning_word, first.start + 0.15,
                                       last.start + last.visible - 0.1))
    return tl
