"""Projects are plain folders: projects/<id>/project.json + assets/. No media in SQLite."""
from __future__ import annotations

import json
import os
import shutil
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from typing import Literal

from pydantic import BaseModel, Field, model_validator


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


ASPECTS = ("16:9", "9:16", "1:1", "4:5")
SecondMode = Literal["blurpad", "native"]


def default_second_aspect(aspect: str) -> str | None:
    """16:9 ↔ 9:16 by default (YouTube + Shorts); square/portrait formats get no second export."""
    return {"16:9": "9:16", "9:16": "16:9"}.get(aspect)


class Project(BaseModel):
    id: str
    title: str
    idea: str = ""
    style: str = ""
    aspect: str = "16:9"                  # main aspect: images are generated natively for it
    second_aspect: str | None = None      # optional extra export (None = main aspect only)
    second_aspect_mode: SecondMode = "blurpad"  # blurpad: main image over a blurred copy (fast)
                                                # native: a second image per shot (~2x image time)
    series: str = ""                      # export "album" tag; empty → the channel name
    description: str = ""                 # export description tag; empty → the script's logline
    moral: str = ""                       # shown on the end card, e.g. "Don't trust those who flatter you"
    # Story Builder (empty cast = a quick video written from an idea)
    cast: list[str] = Field(default_factory=list)                 # character ids from the library
    narrator: dict[str, Any] = Field(default_factory=lambda: {"voice": "af_heart", "speed": 0.85, "pitch": 0.0})
    dialogue_captions: bool = True        # show spoken lines as "Fox: What a beautiful bird!"
    story_mode: Literal["", "paste", "idea"] = ""
    story_text: str = ""                  # paste: the author's words, spoken exactly as written
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    data: dict[str, Any] = Field(default_factory=dict)  # script, shots, etc. (later milestones)

    @model_validator(mode="before")
    @classmethod
    def _defaults(cls, v: Any) -> Any:
        if isinstance(v, dict) and "second_aspect" not in v:
            v = {**v, "second_aspect": default_second_aspect(v.get("aspect", "16:9"))}
        return v

    @model_validator(mode="after")
    def _check_aspects(self) -> "Project":
        if self.aspect not in ASPECTS:
            raise ValueError(f"aspect must be one of {ASPECTS}")
        if self.second_aspect is not None and self.second_aspect not in ASPECTS:
            raise ValueError(f"second_aspect must be one of {ASPECTS} or null")
        if self.second_aspect == self.aspect:
            self.second_aspect = None
        return self


_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]
    return f"{s or 'project'}-{uuid.uuid4().hex[:6]}"


class ProjectStore:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def _dir(self, project_id: str) -> Path:
        if not _ID_RE.match(project_id):
            raise ValueError(f"invalid project id: {project_id!r}")
        return self.root / project_id

    def assets_dir(self, project_id: str) -> Path:
        return self._dir(project_id) / "assets"

    def create(self, title: str, **fields: Any) -> Project:
        p = Project.model_validate({"id": _slug(title), "title": title, **fields})
        self.assets_dir(p.id).mkdir(parents=True, exist_ok=False)
        self._write(p)
        return p

    def get(self, project_id: str) -> Project:
        path = self._dir(project_id) / "project.json"
        if not path.exists():
            raise KeyError(project_id)
        return Project.model_validate_json(path.read_text())

    def list(self) -> list[Project]:
        out = []
        for d in sorted(self.root.iterdir()):
            if not d.name.startswith(".") and (d / "project.json").exists():
                out.append(Project.model_validate_json((d / "project.json").read_text()))
        return out

    SETTINGS = ("title", "idea", "style", "aspect", "second_aspect", "second_aspect_mode", "series", "description", "moral",
                "cast", "narrator", "dialogue_captions", "story_mode", "story_text")

    def update_settings(self, project_id: str, changes: dict[str, Any]) -> Project:
        """Change project settings (validated). Unknown keys are rejected."""
        bad = set(changes) - set(self.SETTINGS)
        if bad:
            raise ValueError(f"not a project setting: {', '.join(sorted(bad))}")
        p = self.get(project_id)
        merged = Project.model_validate({**p.model_dump(), **changes})
        return self.save(merged)

    def duplicate(self, project_id: str, title: str | None = None) -> Project:
        """Copy script, images and narration into a new project. Render cache/exports are skipped
        (large, and cheap to rebuild)."""
        src = self.get(project_id)
        new = Project.model_validate({**src.model_dump(), "title": title or f"{src.title} (copy)",
                                      "id": _slug(title or src.title), "created_at": _now()})
        new.data.pop("exports", None)
        shutil.copytree(self._dir(src.id), self._dir(new.id),
                        ignore=shutil.ignore_patterns("render", "exports", "*.part", "*.part.*"))
        return self.save(new)

    def delete(self, project_id: str) -> None:
        d = self._dir(project_id)
        if not (d / "project.json").exists():
            raise KeyError(project_id)
        trash = d.with_name(f".deleting-{d.name}")  # rename first so a half-deleted folder never lists
        d.rename(trash)
        shutil.rmtree(trash)

    def save(self, p: Project) -> Project:
        p.updated_at = _now()
        self._write(p)
        return p

    def _write(self, p: Project) -> None:
        path = self._dir(p.id) / "project.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(p.model_dump(), indent=2))
        os.replace(tmp, path)  # atomic
