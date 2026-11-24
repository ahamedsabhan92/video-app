"""Shot → motion prompt for image→video: who does what, the camera, and that nothing else may change."""
from __future__ import annotations

from app.characters.model import Character
from app.images.story import _noun, framing_of
from app.script.schema import Shot
from app.story.props import mouth_of, size_of, with_size
from app.styles import style_prompt

CAMERA = {"push in": "The camera slowly pushes in", "pull out": "The camera slowly pulls back", "pan left": "The camera pans slowly to the left",
          "pan right": "The camera pans slowly to the right", "tilt down": "The camera tilts down slowly", "tilt up": "The camera tilts up slowly"}


def camera_words(motion: str) -> str:
    m = motion.lower()
    return next((v for k, v in CAMERA.items() if k in m), "The camera is steady")


def _who(c: Character) -> str:
    f = c.fields
    colour = f.get("colour") or f.get("hair") or ""
    return f"{c.name}, the {_noun(c)}" + (f" with {colour}" if colour else "")


def motion_prompt(shot: Shot, cast: dict[str, Character], style: str) -> str:
    parts = [f"{style_prompt(style).get('lead', 'Animated film')}, children's animated film, {framing_of(shot)} shot."]
    speakers = {l.speaker for l in shot.lines}
    for e in shot.cast:
        c = cast.get(e.name)
        if not c:
            continue
        s = f"{_who(c)}: {e.action.strip().rstrip('.')}" if e.action and not shot.animation else _who(c)
        if e.holding:
            s += f"; keeps holding {with_size(e.holding, size_of(e))}" + (f", the {mouth_of(e.holding)} stays closed around it" if mouth_of(e.holding) else "")
        elif e.name in speakers:
            s += "; talks, the mouth opens and closes in speech" if "sing" not in f"{e.action} {e.expression}" else "; sings, the mouth opens and closes"
        if e.expression:
            s += f"; looks {e.expression}"
        parts.append(s + ".")
    if shot.animation.strip():
        parts.append(shot.animation.strip().rstrip(".") + ".")
    if shot.visual_prompt and not shot.animation:
        parts.append(shot.visual_prompt.strip().rstrip(".") + ".")
    parts.append("Natural living movement: breathing, blinking, small head, wing and tail movements; leaves and grass move softly in the breeze.")
    parts.append(f"{camera_words(shot.motion)}. One continuous shot, smooth motion, the same characters and the same place throughout, nobody else appears.")
    return " ".join(parts)
