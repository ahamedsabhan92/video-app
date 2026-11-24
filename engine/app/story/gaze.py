"""Where the characters look. Order: the "looking at" field → what the action says → something that falls or flies →
whoever is speaking → the other character (the one holding something first) → what lies in the picture → straight ahead.

A character outside the picture is never named (the video model would draw them): then only the direction is said.
"""
from __future__ import annotations

import re

from app.script.schema import CastEntry, Shot

AHEAD = "ahead"
_LOOKS = re.compile(r"\b(?:looks?|looking|gaz(?:es|ing)|star(?:es|ing)|eyes (?:are )?fixed|watch(?:es|ing)?)\s+(?:up |down |over )?(?:at|on|to|toward|towards|into)?\s*"
                    r"((?:the|a|an|his|her|its|their)\s+[\w' -]+?)(?=$|[,.;]| with| and| in front)", re.I)
_MOVES = re.compile(r"((?:the|a|an)\s+[\w' -]+?)\s+(?:is\s+)?(?:falls?|falling|drops?|fl(?:y|ies)|flying|rolls?|rolling|floats?|floating|bounces?)\b", re.I)
_LIES = re.compile(r"^((?:the|a|an)\s+[\w' -]+?)\s+(?:lies?|lying|stands?|sits?|hangs?|is|on|in)\b", re.I)
_CLOSED = re.compile(r"\b(closed eyes|eyes (?:\w+ )?closed|eyes shut|asleep|sleep(?:s|ing))\b", re.I)
_UP = re.compile(r"\b(branch|tree|upper|roof|above|sky|top of|window)\b", re.I)
_DOWN = re.compile(r"\b(grass|ground|lower|below|floor|underneath|meadow)\b", re.I)


def own_text(shot: Shot, e: CastEntry) -> str:
    """What the story says about this character only."""
    said = [s for s in re.split(r"(?<=[.!?])\s+", f"{shot.animation}") if re.search(rf"\b{re.escape(e.name)}\b", s, re.I)]
    return " ".join([e.action, e.expression, *said])


def eyes_closed(shot: Shot, e: CastEntry) -> bool:
    return bool(_CLOSED.search(own_text(shot, e)))


def _height(e: CastEntry) -> int:
    where = re.sub(r"\b(?:looks?|looking|lifted|nose|eyes|steps? back|away)\b[^,.;]*", "", e.action, flags=re.I)     # not what they look at
    return -1 if _DOWN.search(where) else 1 if _UP.search(where) else 0


def _side(e: CastEntry) -> int:
    return -1 if re.search(r"\bleft\b", e.action, re.I) else 1 if re.search(r"\bright\b", e.action, re.I) else 0


def target(shot: Shot, e: CastEntry) -> str:
    """Name of a character, words for a thing, or AHEAD."""
    names = {o.name.lower(): o.name for o in shot.cast if o is not e}

    def known(text: str) -> str:
        t = re.sub(r"^(?:the|a|an)\s+", "", text.strip(), flags=re.I)
        return names.get(t.lower(), text.strip())
    if e.looking_at.strip():
        return known(e.looking_at)
    moving = _MOVES.search(shot.visual_prompt)
    if moving:
        return re.sub(r"^(?:a|an)\s", "the ", re.sub(r"^\w+", lambda m: m.group(0).lower(), moving.group(1)))
    m = _LOOKS.search(e.action)
    if m:
        return known(m.group(1))
    speakers = [l.speaker for l in shot.lines if l.speaker.lower() in names]
    if speakers:
        return speakers[0]
    others = sorted((o for o in shot.cast if o is not e), key=lambda o: not o.holding)
    if others:
        return others[0].name
    lies = _LIES.search(shot.visual_prompt)
    return re.sub(r"^\w+", "the", lies.group(1)) if lies else AHEAD


def gaze_words(shot: Shot, e: CastEntry, whole: Shot | None = None) -> str:
    """"looks up at the Crow". whole: the full shot, when `shot` is a closer view of it."""
    whole = whole or shot
    me = next((o for o in whole.cast if o.name == e.name), e)
    t = target(whole, me)
    if t == AHEAD:
        return "looks straight ahead"
    if t.lower() in ("the audience", "audience", "the camera", "camera", "the viewer", "viewer"):
        return "looks into the camera"
    other = next((o for o in whole.cast if o.name == t), None)
    if not other:
        if whole is not shot and not shot.visual_prompt and _MOVES.search(whole.visual_prompt):
            return f"looks {'down' if _height(me) > 0 else 'up'}"
        verb = "eyes follow" if _MOVES.search(whole.visual_prompt) and t in whole.visual_prompt.lower() else "looks at"
        return f"{verb} {t}"
    dy, dx = _height(other) - _height(me), _side(other) - _side(me)
    up = "up " if dy > 0 else "down " if dy < 0 else ""
    if any(o.name == t for o in shot.cast):
        return f"looks {up}at {t}"
    side = "to the right" if dx > 0 else "to the left" if dx < 0 else ""
    return f"looks {up}{side}".strip() if up or side else "looks to the side"

