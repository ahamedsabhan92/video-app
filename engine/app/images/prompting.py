"""Shot → image prompt: visual prompt + fixed character looks + place + style preset."""
from __future__ import annotations

import hashlib

from app.script.schema import Script, Shot
from app.styles import style_prompt

# ~1 MP, multiples of 16 (FLUX.2 latent grid). Upscaled to 1080p/1920 at render time.
SIZES = {"16:9": (1360, 768), "9:16": (768, 1360), "1:1": (1024, 1024), "4:5": (912, 1136)}


def size_for(aspect: str) -> tuple[int, int]:
    return SIZES.get(aspect, SIZES["16:9"])


def shot_seed(project_id: str, shot_id: str, take: int = 0) -> int:
    """Stable seed per shot (same prompt → same image); bump `take` to get a new variation."""
    h = hashlib.sha256(f"{project_id}:{shot_id}:{take}".encode()).digest()
    return int.from_bytes(h[:4], "big")


# FLUX.2 klein runs at cfg 1 (no negative prompt), so the "don'ts" go in the prompt itself.
# Painterly styles like to add an artist's signature in a corner. Tested on 3 seeds: "no signature"
# alone still gave 2/3 signed; describing a full-bleed, unsigned picture cut it to 1/3 without the
# white vignette borders that "plain margins" causes. Regenerate a shot if one still slips through.
NEGATIVE_SUFFIX = ("Full-bleed image filling the whole frame edge to edge, unsigned. "
                   "No signature, no watermark, no text, no letters, no logo.")

FRAMING_HINTS = {
    "9:16": "Vertical frame: keep the subject centred, full figure visible, with headroom.",
    "4:5": "Portrait frame: keep the subject centred.",
}


def compose(shot: Shot, script: Script, style_key: str, aspect: str = "16:9") -> str:
    style = style_prompt(style_key)
    looks = {c.name: c.description for c in script.characters}
    # The medium goes first: FLUX follows early words most (paper cut-out was ignored at the end).
    parts = [f"{style.get('lead', 'Image')}: {shot.visual_prompt.strip().rstrip('.')}."]
    for name in shot.characters:
        if name in looks:
            parts.append(f"{name}: {looks[name].strip().rstrip('.')}.")
    if shot.location:
        parts.append(f"Setting: {shot.location}.")
    if aspect in FRAMING_HINTS:
        parts.append(FRAMING_HINTS[aspect])
    parts.append(f"Style: {style['prompt']}.")
    parts.append(NEGATIVE_SUFFIX)  # captions are added at edit time
    return " ".join(parts)
