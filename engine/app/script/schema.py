"""Script JSON: title, characters, music, scenes, shots (narration + visual prompt + duration)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

MusicMood = Literal["cheerful", "calm", "playful", "gentle", "adventurous", "dreamy", "triumphant", "curious"]


class Line(BaseModel):
    """One spoken line (Story Builder). speaker "Narrator" or a character's name."""
    speaker: str
    text: str
    emotion: str = ""            # kept for the Shot Editor; the voice doesn't act it out yet


class Location(BaseModel):
    """A place, described once; its empty picture is the background of every shot there."""
    name: str
    description: str = ""


PropSize = Literal["", "small", "medium", "large"]      # "" = automatic


class CastEntry(BaseModel):
    """A character in one shot: what they hold, how they look, which outfit (Story Builder)."""
    name: str
    action: str = Field(default="", description="What they do and where, e.g. 'perched on the long branch'.")
    holding: str = Field(default="", description="Prop the character holds, e.g. 'a wedge of cheese in its beak'.")
    prop_size: PropSize = ""     # of what is held; automatic: small in a beak or mouth, else from the words, else medium
    expression: str = Field(default="", description="e.g. 'proud', 'sly', 'surprised'.")
    looking_at: str = ""         # a character, a thing, "the camera"; empty = automatic (story/gaze.py)
    outfit: str = Field(default="", description="Named outfit from the character library; empty = everyday look.")


class Shot(BaseModel):
    id: str = ""
    narration: str = Field(default="", description="One short sentence the narrator says during this shot.")
    visual_prompt: str = Field(default="", description="What the image shows: who, ONE action, where, framing. No style words.")
    motion: str = Field(default="", description="One camera or subject movement, e.g. 'slow push in'.")
    characters: list[str] = Field(default_factory=list, description="Names of characters visible in the shot.")
    location: str = Field(default="", description="Short reusable place name.")
    sfx: str = Field(default="", description="One short everyday sound, or empty.")
    duration_s: float = 0.0
    on_screen_text: str = Field(default="", description="Optional short on-screen text for this shot, usually empty.")
    cast: list[CastEntry] = Field(default_factory=list, description="Per character: holding, expression, outfit.")
    # Story Builder: spoken lines in order (narrator and characters); empty for M1 scripts, which use narration.
    lines: list[Line] = Field(default_factory=list)
    framing: str = ""            # "wide" | "medium" | "close-up"
    animation: str = ""          # movement in words for the clip (empty = from the cast's actions)
    animation_closer: str = ""   # movement in the closer view, if the shot is long enough to get one (empty = from animation)
    closer_on: str = ""          # whom the closer view shows (empty = who speaks then, else who is at `focus`)
    prop_size: PropSize = ""     # of the thing in visual_prompt ("Also in the picture"), compared with the character
    focus: str = "centre"        # which part of the place a medium / close-up shows

    def spoken(self) -> list[Line]:
        if self.lines:
            return [l for l in self.lines if l.text.strip()]
        return [Line(speaker="Narrator", text=self.narration)] if self.narration.strip() else []


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
    locations: list[Location] = Field(default_factory=list)

    def location(self, name: str) -> Location | None:
        return next((l for l in self.locations if l.name.strip().lower() == name.strip().lower()), None)

    @property
    def shots(self) -> list[Shot]:
        return [s for sc in self.scenes for s in sc.shots]

    @property
    def duration_s(self) -> float:
        return round(sum(s.duration_s for s in self.shots), 2)


STORY_ONLY = ("cast", "lines", "framing", "focus", "animation", "animation_closer", "closer_on", "prop_size")


def llm_schema() -> dict:
    """JSON schema handed to the LLM (no ids/durations — we compute those)."""
    schema = Script.model_json_schema()
    for name in ("Shot", "Scene"):
        props = schema["$defs"][name]["properties"]
        props.pop("id", None)
        props.pop("duration_s", None)
        for k in STORY_ONLY:     # set in the Story Builder, not by the M1 script writer
            props.pop(k, None)
    schema["properties"].pop("locations", None)
    for name in ("CastEntry", "Line", "Location"):
        schema["$defs"].pop(name, None)
    # Make every field required: with structured output the LLM skips optional fields.
    # (Empty strings are still allowed, e.g. a shot with no sfx.)
    for obj in [schema, *schema["$defs"].values()]:
        obj["required"] = list(obj["properties"])
    schema["properties"]["characters"]["minItems"] = 1
    schema["properties"]["music"].pop("default", None)
    return schema

