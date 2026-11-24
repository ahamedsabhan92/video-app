"""Story Builder pictures: place → camera → one pass per character.

1. Every location gets one EMPTY picture ("plate"), made once. It keeps the oak tree the same tree.
   (An earlier shot as reference would copy its characters into the new one.)
2. Camera: wide = the whole plate; medium / close-up = a crop of it, done in code. Asked in words,
   the model kept the plate's full view for a "close-up".
3. Characters are added one at a time by editing the picture so far (image 1) with that character's
   locked reference (image 2). Several character references in one generation mixed them up and gave
   the crow's cheese to the fox.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.characters.model import Character, CharacterStore
from app.characters.prompts import _anatomy, expression_cue, four_legged
from app.images.prompting import NEGATIVE_SUFFIX
from app.render.ffmpeg import run
from app.script.schema import CastEntry, Location, Script, Shot
from app.story.gaze import gaze_words
from app.story.props import compared, mouth_of, size_of, with_size
from app.styles import style_prompt

CROP = {"wide": 1.0, "medium": 0.6, "close-up": 0.42}
CROP_TWO = 0.85            # two or more in a medium shot: keep branch AND ground in the picture
FOCUS_XY = {"centre": (.5, .5), "left": (.08, .5), "right": (.92, .5), "upper left": (.08, .12), "upper right": (.92, .12),
            "lower left": (.15, .97), "lower right": (.85, .97)}


@dataclass
class Pass:
    prompt: str
    refs: list[Path]       # [character reference]; the picture so far is added as image 1
    name: str


def framing_of(shot: Shot) -> str:
    f = shot.framing if shot.framing in CROP else "wide"
    return "medium" if f == "close-up" and len(shot.cast) > 1 else f


def missing_references(project_style: str, cast: list[Character], script: Script | None = None) -> list[str]:
    """What is needed before pictures can be made, in words for the user."""
    label = style_prompt(project_style).get("label", project_style)
    out = [f"{c.name} has no locked character sheet in {label}" for c in cast
           if not (c.looks.get(project_style) and c.looks[project_style].locked)]
    for s in script.shots if script else []:
        for e in s.cast:
            if e.name not in {c.name for c in cast}:     # renamed in the library, or not in this story's cast
                msg = f"“{e.name}” ({s.id}) isn't one of this story's characters"
                if not any(m.startswith(f"“{e.name}”") for m in out):
                    out.append(msg)
                continue
            c = next((c for c in cast if c.name == e.name), None)
            o = c.outfit(e.outfit) if c and e.outfit else None
            if o and not o.refs.get(project_style):
                msg = f"{e.name}'s outfit “{o.name}” has no reference image in {label}"
                if msg not in out:
                    out.append(msg)
    return out


def plate_prompt(loc: Location, style: str) -> str:
    st = style_prompt(style)
    return (f"{st.get('lead', 'Image')}: wide shot of {loc.name}: {loc.description.rstrip('.')}. An empty place: no people, "
            f"no animals, no characters. Style: {st['prompt']}. {NEGATIVE_SUFFIX}")


def _noun(c: Character) -> str:
    f = c.fields
    if c.kind == "human":
        return f.get("gender") or "person"
    return f.get("species") or c.kind


def _size(framing: str, n: int) -> str:
    if framing == "close-up":
        return "Extreme close-up portrait, cropped at the chest: only head and chest are visible and fill the picture"
    if framing == "medium":
        return ("medium shot: the whole body is visible and large, about two thirds of the picture height" if n == 1 else
                "medium shot: the whole body is visible, about half of the picture height")
    return "wide shot: the whole body is visible, about one third of the picture height"


def _holding(e: CastEntry) -> str:
    size, mouth = size_of(e), mouth_of(e.holding)
    grip = f" The {mouth} is closed around it, so it clearly touches the {mouth}." if mouth else ""
    return f"{e.name} holds {with_size(e.holding, size)}. {compared(size, e.name, mouth)}{grip}"


def reference(store: CharacterStore, c: Character, e: CastEntry, style: str, framing: str) -> Path:
    look = c.looks.get(style)
    if not look or not look.locked:
        raise ValueError(f"{c.name} has no locked character sheet in this style. Open Characters and lock one.")
    v = look.locked.views
    outfit = c.outfit(e.outfit) if e.outfit else None
    if outfit:
        if not outfit.refs.get(style):
            raise ValueError(f"{c.name}'s outfit “{outfit.name}” has no reference image in this style. Make it in Characters.")
        return store.path(c, outfit.refs[style])
    if framing == "close-up":
        expr = f"expr:{e.expression.strip().lower()}"
        if expr in v and not e.holding:       # the acted expression itself; with a prop the neutral face works better
            return store.path(c, v[expr])
        return store.path(c, v.get("front") or v["full_body"])
    return store.path(c, v["full_body"])


_WHERE = re.compile(r"^(?:up\s+|down\s+)?(?:on|at|in|under|near|by|below|above|beside|next to|among|behind|inside|lower|upper)\b", re.I)


def _action(e: CastEntry, framing: str, later: list[str]) -> str:
    """A face close-up without the spot ("on the long branch" pulls the camera back); and characters that
    are added in a later pass are not named yet, or the model draws them now and again later."""
    clauses = [c.strip() for c in e.action.split(",") if c.strip()]
    if framing == "close-up":
        clauses = [c for c in clauses if not _WHERE.match(c)] or clauses[-1:]
    text = ", ".join(clauses).rstrip(".")
    for n in later:
        text = re.sub(rf"\b(?:the\s+)?{re.escape(n)}(?:'s)?\b", "someone nearby", text, flags=re.I)
    return text


def _body(c: Character, e: CastEntry, framing: str) -> str:
    out = ""
    if framing != "close-up" and four_legged(c) and not re.search(r"\b(jump|leap|sit|lie|lying|run|hind|climb|rear)", e.action, re.I):
        out += f" All four paws of {e.name} are on the ground."
    if re.search(r"\bbeak\b", e.holding, re.I) and not re.search(r"\b(wing|fl(?:y|ies|ew|ight))", e.action, re.I):
        out += f" {e.name}'s wings are folded and both feet stand firmly below; only the beak holds it."
    return out


def story_passes(shot: Shot, cast: dict[str, Character], store: CharacterStore, style: str) -> list[Pass]:
    st = style_prompt(style)
    tail = f"{st.get('lead', 'Image')}. Style: {st['prompt']}. {NEGATIVE_SUFFIX}"
    framing = framing_of(shot)
    people = [e for e in shot.cast if e.name in cast]
    # whoever holds a prop first, then whoever the others look at or talk about
    seen = list(people)
    people.sort(key=lambda e: (not e.holding, -sum(bool(re.search(rf"\b{re.escape(e.name)}\b", o.action, re.I)) for o in seen if o is not e)))
    someone_holds = any(e.holding for e in people)
    out: list[Pass] = []
    for i, e in enumerate(people):
        c = cast[e.name]
        parts = [f"Add {e.name}, the {_noun(c)} from image 2, to image 1. {_size(framing, len(people))}.".replace("  ", " ")]
        act = _action(e, framing, [p.name for p in people[i + 1:]])
        if act:
            parts.append(f"{e.name}: {act}.{_body(c, e, framing)}")
        if e.holding:
            parts.append(_holding(e))
        elif someone_holds:
            parts.append(f"{e.name} holds nothing.")
        if e.looking_at.strip():          # chosen by hand; without it the action says it (as in pictures made before)
            parts.append(f"{e.name} {gaze_words(shot, e)}, with both eyes turned the same way.")
        if e.expression:
            parts.append(f"{e.name} looks {e.expression}: {expression_cue(e.expression)}.")
        if i == 0 and shot.visual_prompt:
            extra = with_size(shot.visual_prompt, shot.prop_size) if shot.prop_size else shot.visual_prompt.strip().rstrip(".")
            parts.append(f"Also in the picture: {extra}." + (f" {compared(shot.prop_size, e.name)}" if shot.prop_size else ""))
        if i == 0:
            parts.append("Keep the place in image 1 exactly the same." if framing == "wide" else "Keep the background of image 1.")
        else:
            before = ", ".join(p.name for p in people[:i])
            parts.append(f"Keep everything else in image 1 exactly the same, including {before} and what {before} holds.")
        parts.append(f"Only one {_noun(c)}.{_anatomy(c)} {tail}")
        out.append(Pass(" ".join(parts), [reference(store, c, e, style, framing)], e.name))
    if not people and shot.visual_prompt:
        out.append(Pass(f"Change image 1: {shot.visual_prompt.strip().rstrip('.')}. Keep the place in image 1 the same. No people, "
                        f"no animals. {tail}", [], ""))
    return out


def camera(plate: Path, shot: Shot, out: Path, width: int, height: int) -> Path:
    """The plate as this shot's camera sees it."""
    framing = framing_of(shot)
    frac = CROP_TWO if framing == "medium" and len(shot.cast) > 1 else CROP[framing]
    fx, fy = FOCUS_XY.get(shot.focus, (.5, .5))
    out.parent.mkdir(parents=True, exist_ok=True)
    vf = f"scale={width}:{height}"
    if frac < 1:
        w, h = int(width * frac) // 2 * 2, int(height * frac) // 2 * 2
        vf += f",crop={w}:{h}:{int((width - w) * fx)}:{int((height - h) * fy)},scale={width}:{height}:flags=lanczos"
        if framing == "close-up":
            vf += ",gblur=sigma=5"      # soft background behind a face
    run(["-i", str(plate), "-vf", vf, "-frames:v", "1", str(out)], label="camera")
    return out


def signature(shot: Shot, script: Script, cast: dict[str, Character], store: CharacterStore, style: str, plate_sig: str) -> str:
    """Changes when anything the picture depends on changes: words, camera, place, locked sheet, outfit."""
    passes = story_passes(shot, cast, store, style)
    refs = []
    for p in passes:
        c = cast.get(p.name)
        refs.append(f"{p.name}:{c.looks[style].locked.version if c else ''}:{p.refs[0].name if p.refs else ''}")
    return "\n".join([*(p.prompt for p in passes), f"refs {refs}", f"camera {framing_of(shot)} {shot.focus} {len(shot.cast)}",
                      f"place {plate_sig}"])
