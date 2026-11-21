"""Script JSON: title, characters, music, scenes, shots (narration + visual prompt + duration)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

MusicMood = Literal["cheerful", "calm", "playful", "gentle", "adventurous", "dreamy", "triumphant", "curious"]


class Shot(BaseModel):
    id: str = ""
    narration: str = Field(description="One short sentence the narrator says during this shot.")
    visual_prompt: str = Field(description="What the image shows: who, ONE action, where, framing. No style words.")
    motion: str = Field(default="", description="One camera or subject movement, e.g. 'slow push in'.")
    characters: list[str] = Field(default_factory=list, description="Names of characters visible in the shot.")
    location: str = Field(default="", description="Short reusable place name.")
    sfx: str = Field(default="", description="One short everyday sound, or empty.")
    duration_s: float = 0.0
    on_screen_text: str = Field(default="", description="Optional short on-screen text for this shot, usually empty.")


class Scene(BaseModel):
    id: str = ""
    title: str
    learning_word: str = Field(default="", description="1-3 word on-screen learning word in CAPITALS, e.g. 'BRUSH TEETH'.")
    shots: list[Shot]


class Character(BaseModel):
    name: str
    description: str = Field(description="Fixed look: age, hair, skin, outfit, colours. Reused verbatim.")


class Music(BaseModel):
    mood: MusicMood = Field(description="Exactly one mood for the whole video.")
    tempo: Literal["slow", "medium", "fast"]
    instruments: str = Field(description="2-3 instruments, e.g. 'ukulele, glockenspiel'.")


class Script(BaseModel):
    title: str
    logline: str = ""
    characters: list[Character] = Field(default_factory=list)
    music: Music = Field(default_factory=lambda: Music(mood="cheerful", tempo="medium", instruments="piano"))
    scenes: list[Scene]

    @property
    def shots(self) -> list[Shot]:
        return [s for sc in self.scenes for s in sc.shots]

    @property
    def duration_s(self) -> float:
        return round(sum(s.duration_s for s in self.shots), 2)


def llm_schema() -> dict:
    """JSON schema handed to the LLM (no ids/durations — we compute those)."""
    schema = Script.model_json_schema()
    for name in ("Shot", "Scene"):
        props = schema["$defs"][name]["properties"]
        props.pop("id", None)
        props.pop("duration_s", None)
    # Make every field required: with structured output the LLM skips optional fields.
    # (Empty strings are still allowed, e.g. a shot with no sfx.)
    for obj in [schema, *schema["$defs"].values()]:
        obj["required"] = list(obj["properties"])
    schema["properties"]["characters"]["minItems"] = 1
    schema["properties"]["music"].pop("default", None)
    return schema

