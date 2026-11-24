"""Shot → motion prompt for image→video: who does what, where they look, who talks, the camera, and that nothing else
may change. The camera stands still: the shot's camera move is added at render (asked to move it, the model shakes it:
2-7 px per frame, against 0.1 with a steady camera). A long shot has several pieces: the whole picture, then closer views of it."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from app.characters.model import Character
from app.images.story import FOCUS_XY, _noun, framing_of
from app.script.schema import CastEntry, Shot
from app.story.gaze import eyes_closed, gaze_words, own_text
from app.story.props import mouth_of, size_of, with_size
from app.styles import style_prompt

_MOUTH_ACTS = re.compile(r"\b(chew\w*|bit(?:e|es|ing)|eat\w*|swallow\w*|gulp\w*|lick\w*|yawn\w*|laugh\w*|sing(?:s|ing)?|pant(?:s|ing)|gasp\w*|shout\w*|roar\w*|"
                         r"catch\w*[^.;]*mouth|(?:mouth|beak)[^.;,]*\bopen\w*|open\w*[^.;,]*(?:mouth|beak))\b", re.I)
_BEAKED = re.compile(r"\b(beak|bird|crow|raven|owl|duck|hen|chicken|rooster|parrot|eagle|dove|pigeon|goose|swan|penguin|sparrow|robin)\b", re.I)
CLOSE_UP = (0.62, .5, .1)      # the closer view of a close-up: the face
CLOSER = 0.56                  # how much of the picture a closer view shows
MIN_PIECE_S = 1.2


def _who(c: Character) -> str:
    f = c.fields
    colour = f.get("colour") or f.get("hair") or ""
    return f"{c.name}, the {_noun(c)}" + (f" with {colour}" if colour else "")


def place_of(e: CastEntry, default: str = "centre") -> str:
    """Where in the picture the action puts them: "on the branch, upper left" → upper left."""
    for k in sorted(FOCUS_XY, key=len, reverse=True):
        if k != "centre" and re.search(rf"\b{k}\b", e.action, re.I):
            return k
    return default


def in_view(shot: Shot) -> list[str]:
    """Who is in the closer view of a shot's picture: those placed where the camera looks (focus). Naming anyone
    else makes the video model invent them."""
    f = shot.focus.strip().lower()
    names = [e.name for e in shot.cast if f and f != "centre" and re.search(rf"\b{re.escape(f)}\b", e.action, re.I)]
    return names or [e.name for e in shot.cast]


def closer_shot(shot: Shot, names: list[str] | None = None) -> Shot:
    """The shot as a closer view of it, on these characters, shows it."""
    keep = names if names is not None else in_view(shot)
    gone = [n for n in (e.name for e in shot.cast) if n not in keep]
    said = [s.strip() for s in re.split(r"(?<=[.!?])\s+", shot.animation_closer.strip() or shot.animation) if s.strip()]
    said = [s for s in said if not any(re.search(rf"\b{re.escape(n)}\b", s, re.I) for n in gone)]
    return shot.model_copy(update={"cast": [e for e in shot.cast if e.name in keep], "animation": " ".join(said), "framing": "close-up",
                                   "lines": [l for l in shot.lines if l.speaker not in gone], "visual_prompt": "", "motion": ""})


# ---- pieces ------------------------------------------------------------------

@dataclass(frozen=True)
class Piece:
    start: float
    seconds: float
    shot: Shot                              # as this piece shows it (who is in view, who speaks meanwhile)
    crop: tuple[float, float, float]        # fraction, x, y of the picture; fraction 1 = all of it


def _moments(lines: list[tuple[str, str, float, float]]) -> list[float]:
    """Good times for a cut: where a spoken line or a sentence in it begins."""
    out = []
    for _, text, start, secs in lines:
        at = 0
        for s in re.split(r"(?<=[.!?])\s+", text):
            out.append(start + secs * at / max(1, len(text)))
            at += len(s) + 1
    return out


def cut_times(need: float, longest: float, hold: float, lines, most: int) -> list[float]:
    """When pieces 2, 3… start: equal shares, moved to the nearest beginning of a sentence if every piece stays short enough."""
    n = max(1, min(most, math.ceil((need - hold) / longest - 1e-6)))
    cuts = [need * k / n for k in range(1, n)]
    marks = _moments(lines)
    for k in range(len(cuts)):
        lo, hi = cuts[k - 1] if k else 0.0, cuts[k + 1] if k + 1 < len(cuts) else need
        room = longest + (hold if k + 1 == len(cuts) else 0.0)
        cuts[k] = min(cuts[k], lo + longest)           # a held frame, if any, comes at the very end
        ok = [m for m in marks if MIN_PIECE_S <= m - lo <= longest and MIN_PIECE_S <= hi - m <= room]
        if ok:
            cuts[k] = min(ok, key=lambda m: abs(m - cuts[k]))
    return cuts


def plan_pieces(shot: Shot, need: float, longest: float, hold: float, lines: list[tuple[str, str, float, float]], most: int) -> list[Piece]:
    """lines: (speaker, text, start in the shot, seconds) of the recorded voices."""
    cuts = [0.0, *cut_times(need, longest, hold, lines, most), need]
    names = [e.name for e in shot.cast]
    out: list[Piece] = []
    last: list[str] = []
    for k, (a, b) in enumerate(zip(cuts, cuts[1:])):
        talk: dict[str, float] = {}
        for who, _, start, secs in lines:
            part = min(b, start + secs) - max(a, start)
            if part > 0.35:
                talk[who] = talk.get(who, 0) + part
        voiced = [l for l in shot.lines if l.speaker in talk] if lines else list(shot.lines)
        if k == 0:
            view, crop = shot, (1.0, .5, .5)
        else:
            loud = max((n for n in talk if n in names), key=lambda n: talk[n], default="")
            who = [shot.closer_on] if k == 1 and shot.closer_on in names else [loud] if loud and talk[loud] >= (b - a) * .4 else in_view(shot)
            if who == last and len(names) > 1:          # not the same view twice in a row
                who = [n for n in names if n not in last]
            view, last = closer_shot(shot, who), who
            e = next(e for e in shot.cast if e.name == who[0]) if len(who) == 1 else None
            crop = CLOSE_UP if framing_of(shot) == "close-up" else (CLOSER, *FOCUS_XY.get(place_of(e, shot.focus) if e else shot.focus, (.5, .5)))
        out.append(Piece(round(a, 3), round(b - a, 3), view.model_copy(update={"lines": [l for l in view.lines if l in voiced]}), crop))
    return out


# ---- words -------------------------------------------------------------------

def _mouth(c: Character, e: CastEntry, shot: Shot, whole: Shot) -> str:
    held = mouth_of(e.holding)
    if e.holding and held:
        return f"keeps holding {with_size(e.holding, size_of(e))}, the {held} stays closed around it"
    word = "beak" if _BEAKED.search(f"{e.holding} {c.fields.get('species', '')} {c.name}") else "mouth"
    own = own_text(whole, e)
    sings = re.search(r"\bsing", own, re.I)
    if e.name in {l.speaker for l in shot.lines}:
        return f"sings, the {word} opens and closes" if sings else f"{word} opens and closes as if talking"
    if _MOUTH_ACTS.search(own):           # they chew, laugh, sing… without saying a line
        return ""
    return f"the {word} stays closed"


def motion_prompt(shot: Shot, cast: dict[str, Character], style: str, whole: Shot | None = None) -> str:
    """whole: the full shot when `shot` is one piece of it."""
    whole = whole or shot
    parts = [f"{style_prompt(style).get('lead', 'Animated film')}, children's animated film, {framing_of(shot)} shot."]
    for e in shot.cast:
        c = cast.get(e.name)
        if not c:
            continue
        words = [f"{_who(c)}: {e.action.strip().rstrip('.')}" if e.action and not shot.animation else _who(c)]
        if e.holding and not mouth_of(e.holding):
            words.append(f"keeps holding {with_size(e.holding, size_of(e))}")
        words.append(_mouth(c, e, shot, whole))
        if e.expression:
            words.append(f"looks {e.expression}")
        if not eyes_closed(whole, e):
            words.append(f"{gaze_words(shot, e, whole)}, blinks naturally, both eyes turned the same way")
        parts.append("; ".join(w for w in words if w) + ".")
    if shot.animation.strip():
        parts.append(shot.animation.strip().rstrip(".") + ".")
    if shot.visual_prompt and not shot.animation:
        parts.append(shot.visual_prompt.strip().rstrip(".") + ".")
    parts.append("Natural living movement: soft breathing, natural blinking, relaxed lively eyes, small slow head and tail movements; "
                 "the leaves stay on the trees and barely move.")
    parts.append("Static camera on a tripod: the background, the tree and the ground stay perfectly still, only the characters move. "
                 "One continuous shot, slow smooth fluid motion, the same characters and the same place throughout, nobody else appears.")
    return " ".join(parts)
