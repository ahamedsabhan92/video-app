"""Character → text (for image prompts and stories)."""
from __future__ import annotations

from app.characters.model import FIELDS, Character, Outfit
from app.styles import style_prompt

BACKGROUND = "plain light grey studio background, soft even lighting, nothing else in the picture"


def describe(c: Character, style: str | None = None, outfit: Outfit | None = None) -> str:
    """One sentence of the fixed look, e.g. "Fox: a slim adult red fox, orange-red fur, …".
    Text edits of the current version ("add a red scarf") are included as look changes."""
    f = {k: v.strip() for k, v in c.fields.items() if v and v.strip()}
    parts: list[str] = []
    if c.kind == "human":
        age = f.get("age", "")
        age = f"{age}-year-old" if age.isdigit() else age
        parts.append("a " + " ".join(x for x in (age, f.get("gender") or "person") if x))
        for key, label in (("height", "height"), ("build", "build"), ("skin", "skin"), ("face_shape", "face"),
                           ("eyes", "eyes"), ("hair", "hair")):
            if f.get(key):
                parts.append(f"{f[key]} {label}")
    else:
        what = " ".join(x for x in (f.get("size"), f.get("age"), f.get("gender"),
                                    f.get("species") or c.kind) if x)
        parts.append(f"a {what}")
        if f.get("stance"):
            parts.append(f"stands {f['stance']}")
        for key, label in (("colour", ""), ("material", "made of"), ("markings", "markings:"), ("eyes", "eyes:")):
            if f.get(key):
                parts.append(f"{label} {f[key]}".strip())
    clothes = outfit.description.strip() if outfit and outfit.description.strip() else f.get("clothes", "")
    if clothes:
        parts.append(f"wearing {clothes}")
    if f.get("accessories"):
        parts.append(f"with {f['accessories']}")
    if c.other.strip():
        parts.append(c.other.strip().rstrip("."))
    if style:
        changes = [v.instruction for v in c.looks.get(style, None).chain()] if style in c.looks else []
        changes = [x.strip().rstrip(".") for x in changes if x and x.strip()]
        if changes:
            parts.append("look changes: " + "; ".join(changes))
    return f"{c.name}: " + ", ".join(p for p in parts if p) + "."


def field_list(kind: str) -> list[tuple[str, str]]:
    return FIELDS.get(kind, FIELDS["human"])


def _lead(style: str) -> str:
    s = style_prompt(style)
    return s.get("lead", "Image")


def _tail(style: str) -> str:
    return f"Style: {style_prompt(style)['prompt']}. No text, no letters, no signature, no watermark."


def look_prompt(c: Character, style: str, photo: bool = False) -> str:
    face = " Base the face on the person in the reference photo." if photo else ""
    # A straight front view made foxes grow two tails (tail visible on both sides): use a slight
    # three-quarter view and say the anatomy out loud for animals.
    return (f"{_lead(style)}: character design reference, one single character, full body, slightly turned "
            f"three-quarter front view, standing, whole body visible from head to feet, centred. "
            f"{describe(c, None)}{face}{_anatomy(c)} {BACKGROUND}. {_tail(style)}")


def _anatomy(c: Character) -> str:
    return " Correct anatomy: one head, exactly one tail." if c.kind == "animal" else ""


def restyle_prompt(c: Character, style: str) -> str:
    return (f"{_lead(style)}: redraw the exact same character from the reference image in this art style. "
            f"Keep the identity: same species, body shape, colours, markings, face, clothes and accessories. "
            f"Full body, slightly turned three-quarter front view, standing, centred. {describe(c, None)}{_anatomy(c)} "
            f"{BACKGROUND}. {_tail(style)}")


# Tested on a crow and a fox: "head and shoulders portrait" still gave full bodies, so the crop is
# spelled out. Expressions are one close-up each (a 4-in-1 sheet was too subtle and once turned the
# crow into human-faced blobs), acted out in an exaggerated cartoon way.
VIEW_PROMPTS = {
    "front": ("Close-up portrait: only the head and upper chest fill the frame, cropped just below the "
              "shoulders, front view, looking at the camera, friendly neutral expression.", (1024, 1024)),
    "three_quarter": ("Close-up portrait: only the head and upper chest fill the frame, cropped just below the "
                      "shoulders, three-quarter view, head turned slightly to the left.", (1024, 1024)),
    "side": ("Full body side view in profile, facing left, standing, one tail.", (1152, 896)),
}
EXPRESSION_SIZE = (896, 896)
EXPRESSION_CUES = {
    "happy": "a huge open smile, eyes squeezed into happy upturned arcs, cheeks raised, bouncing with joy",
    "sad": "big droopy teary eyes, brows tilted up in the middle, head hanging low, mouth or beak turned down",
    "surprised": "wide-eyed shock: eyes huge and round with tiny pupils, mouth or beak wide open, head jerked "
                 "back, fur or feathers standing on end",
    "angry": "a deep angry frown, brows slammed down into a sharp V, glaring eyes, teeth or beak clenched, "
             "leaning forward",
    "sly": "sly half-closed eyes glancing sideways, one brow raised, a crooked one-sided smirk",
    "proud": "chest puffed out, chin raised high, eyes half-closed smugly, a self-satisfied smile",
    "singing": "mouth or beak wide open singing loudly, eyes closed in bliss, head tilted up",
    "scared": "trembling, eyes huge with fear, shoulders hunched, mouth or beak open in a gasp",
    "laughing": "laughing out loud, eyes shut tight, mouth or beak wide open, body shaking",
}


def expression_cue(word: str) -> str:
    w = word.strip().lower()
    return EXPRESSION_CUES.get(w, f"an unmistakable, exaggerated {w} expression and body language, "
                                  f"readable at a glance")


def view_keys(c: Character) -> list[str]:
    return [*VIEW_PROMPTS, *(f"expr:{e.strip().lower()}" for e in c.expressions if e.strip())]


def expression_prompt(c: Character, style: str, word: str) -> str:
    return (f"{_lead(style)}: the exact same character as in the reference image, same identity, colours, "
            f"markings, clothes and accessories. Close-up of the head and upper body, exaggerated cartoon "
            f"acting like a classic animated film: {expression_cue(word)}. The character keeps its real "
            f"anatomy (same beak or muzzle, eyes and markings; never a human face). {describe(c, style)} "
            f"{BACKGROUND}. {_tail(style)}")


def view_prompt(c: Character, style: str, view: str) -> tuple[str, tuple[int, int]]:
    if view.startswith("expr:"):
        return expression_prompt(c, style, view[5:]), EXPRESSION_SIZE
    what, size = VIEW_PROMPTS[view]
    return (f"{_lead(style)}: the exact same character as in the reference image, same identity, colours, "
            f"markings, clothes and accessories. {what} {describe(c, style)} {BACKGROUND}. {_tail(style)}", size)


def edit_prompt(c: Character, style: str, instruction: str) -> str:
    return (f"{instruction.strip().rstrip('.')}. Keep everything else about the character exactly the same: "
            f"identity, pose, framing, colours and background. {_tail(style)}")


def outfit_prompt(c: Character, style: str, outfit: Outfit) -> str:
    return (f"{_lead(style)}: the exact same character as in the reference image, same identity, face, "
            f"colours and markings, now wearing {outfit.description or outfit.name}. Full body, slightly turned "
            f"three-quarter front view, standing, centred.{_anatomy(c)} {BACKGROUND}. {_tail(style)}")


FULL_BODY = (896, 1152)
