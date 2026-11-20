"""Projects are plain folders: projects/<id>/project.json + assets/. No media in SQLite."""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Project(BaseModel):
    id: str
    title: str
    idea: str = ""
    style: str = ""
    aspect: str = "16:9"
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    data: dict[str, Any] = Field(default_factory=dict)  # script, shots, etc. (later milestones)


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
        p = Project(id=_slug(title), title=title, **fields)
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
            if (d / "project.json").exists():
                out.append(Project.model_validate_json((d / "project.json").read_text()))
        return out

    def save(self, p: Project) -> Project:
        p.updated_at = _now()
        self._write(p)
        return p

    def _write(self, p: Project) -> None:
        path = self._dir(p.id) / "project.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(p.model_dump(), indent=2))
        os.replace(tmp, path)  # atomic
