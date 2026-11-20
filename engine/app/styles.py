"""Built-in style presets (original descriptions — no references to existing shows/brands)."""
from __future__ import annotations

STYLES: dict[str, dict[str, str]] = {
    "3d-cartoon": {
        "label": "3D cartoon",
        "prompt": "stylised 3D animated render, soft rounded shapes, big expressive eyes, warm "
                  "global illumination, saturated friendly colours, clean background, family film look",
        "negative": "photo, realistic skin, text, watermark, logo, extra fingers, deformed",
    },
    "anime": {
        "label": "Anime",
        "prompt": "2D anime illustration, clean line art, cel shading, vibrant colours, detailed "
                  "painted background, soft lighting",
        "negative": "3d render, photo, text, watermark, logo, extra fingers, deformed",
    },
    "realistic": {
        "label": "Realistic",
        "prompt": "photorealistic, natural light, 35mm lens, shallow depth of field, high detail",
        "negative": "cartoon, illustration, text, watermark, logo, extra fingers, deformed",
    },
    "watercolour": {
        "label": "Watercolour",
        "prompt": "hand-painted watercolour storybook illustration, soft washes, visible paper "
                  "texture, gentle pastel palette, loose ink outlines",
        "negative": "photo, 3d render, harsh shadows, text, watermark, logo, deformed",
    },
    "paper-cutout": {
        "label": "Paper cut-out",
        "prompt": "layered paper cut-out diorama, craft paper textures, soft drop shadows, "
                  "bright cheerful colours, handmade look",
        "negative": "photo, glossy, text, watermark, logo, deformed",
    },
}

DEFAULT_STYLE = "3d-cartoon"


def style_prompt(key: str) -> dict[str, str]:
    return STYLES.get(key, STYLES[DEFAULT_STYLE])
