"""How big a prop is, compared with who holds it. The picture model takes "a big wedge of cheese" literally
(a block as big as the crow), so the size is said in words it can see: next to the beak, the head, the body."""
from __future__ import annotations

import re

from app.script.schema import CastEntry

_BIG = re.compile(r"\b(?:very |really |quite )?(?:big|large|huge|giant|enormous|massive|gigantic)\b\s*", re.I)
_SMALL = re.compile(r"\b(?:very |really |quite )?(?:small|tiny|little)\b\s*", re.I)
_MOUTH = re.compile(r"\b(beak|mouth|teeth)\b", re.I)


def size_of(e: CastEntry) -> str:
    """The chosen size, or the automatic one: what is carried in a beak or mouth has to be small."""
    if e.prop_size:
        return e.prop_size
    if _MOUTH.search(e.holding):
        return "small"
    return "large" if _BIG.search(e.holding) else "small" if _SMALL.search(e.holding) else "medium"


def with_size(text: str, size: str) -> str:
    """"A big wedge of cheese in her beak", small → "a small wedge of cheese in her beak"."""
    t = re.sub(r"\s+", " ", _SMALL.sub("", _BIG.sub("", text))).strip().rstrip(".")
    word = {"small": "small", "large": "large"}.get(size, "")
    m = re.match(r"(an?|the|his|her|its|their|one)\s+(.*)", t, re.I)
    art, rest = (m.group(1).lower(), m.group(2)) if m else ("", t)
    if art in ("a", "an"):
        first = word or rest
        art = "an" if first[:1].lower() in "aeiou" else "a"
    return " ".join(x for x in (art, word, rest) if x)


def compared(size: str, name: str, mouth: str = "") -> str:
    """Size sentence for the picture."""
    if size == "small":
        if mouth:
            return f"It is a tiny bite-sized piece, shorter than {name}'s {mouth} and much smaller than {name}'s head."
        return f"It is little: much smaller than {name}'s head."
    if size == "large":
        return f"It is large: about half as tall as {name}."
    return f"It is about as big as {name}'s head."


def mouth_of(text: str) -> str:
    m = _MOUTH.search(text)
    return {"teeth": "mouth"}.get(m.group(1).lower(), m.group(1).lower()) if m else ""
