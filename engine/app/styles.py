"""Built-in style presets (original descriptions — no references to existing shows/brands).

lead:   the medium, put FIRST in the image prompt (FLUX weighs early words most)
prompt: the look in detail, added at the end
Checked 2026-11-21 by rendering the same two shots in every preset (scratchpad style sheets):
each must be recognisably different from the others.
"""
from __future__ import annotations

STYLES: dict[str, dict[str, str]] = {
    "3d-cartoon": {
        "label": "3D cartoon",
        "lead": "Stylised 3D animated film still",
        "prompt": "soft rounded shapes, big expressive eyes, warm global illumination, saturated "
                  "friendly colours, clean background, family film look",
        "negative": "photo, realistic skin, text, watermark, logo, extra fingers, deformed",
    },
    "anime": {
        "label": "Anime",
        "lead": "Japanese-style 2D anime illustration",
        "prompt": "crisp black line art, flat cel shading with hard-edged shadows, large detailed "
                  "eyes with highlights, vivid colours, hand-painted detailed background, "
                  "TV anime key visual look",
        "negative": "3d render, photo, text, watermark, logo, extra fingers, deformed",
    },
    "realistic": {
        "label": "Realistic",
        "lead": "Photograph",
        "prompt": "photorealistic, natural light, 35mm lens, shallow depth of field, high detail, "
                  "real textures",
        "negative": "cartoon, illustration, text, watermark, logo, extra fingers, deformed",
    },
    "watercolour": {
        "label": "Watercolour",
        "lead": "Hand-painted watercolour storybook illustration",
        "prompt": "soft washes, visible paper texture, gentle pastel palette, loose ink outlines",
        "negative": "photo, 3d render, harsh shadows, text, watermark, logo, deformed",
    },
    "paper-cutout": {
        "label": "Paper cut-out",
        "lead": "Handmade layered paper cut-out diorama, photographed",
        "prompt": "every person, object and background element cut from coloured cardstock and "
                  "craft paper, visible paper fibres and slightly rough cut edges, flat paper "
                  "layers stacked at different depths casting soft shadows on each other, no "
                  "outlines, handmade craft look",
        "negative": "photo, glossy, text, watermark, logo, deformed",
    },
}

DEFAULT_STYLE = "3d-cartoon"


def style_prompt(key: str) -> dict[str, str]:
    return STYLES.get(key, STYLES[DEFAULT_STYLE])
