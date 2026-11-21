"""Creator profile (Settings screen): who made the videos. Stored in data/settings.json.

Used to tag every export (artist, album, copyright, encoder) and to choose the export folder.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

APP_NAME = "Seekaas Studio"


class CreatorProfile(BaseModel):
    creator_name: str = "Ahamed Sabhan"
    channel_name: str = "Tiny Tales"
    copyright: str = "© {year} Ahamed Sabhan. All rights reserved."   # {year} → current year
    website: str = ""
    export_folder: str = "~/Movies/Seekaas Studio/<project>/"        # <project> → project title


def _path(data_dir: Path) -> Path:
    return data_dir / "settings.json"


def load_profile(data_dir: Path) -> CreatorProfile:
    p = _path(data_dir)
    if p.exists():
        return CreatorProfile.model_validate(json.loads(p.read_text()).get("creator", {}))
    return CreatorProfile()


def save_profile(data_dir: Path, profile: CreatorProfile) -> CreatorProfile:
    p = _path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(p.read_text()) if p.exists() else {}
    data["creator"] = profile.model_dump()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(p)
    return profile


def safe_name(text: str) -> str:
    """A folder name from a project title (keeps spaces and apostrophes; no slashes etc.)."""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", text).strip().strip(".")
    return re.sub(r"\s+", " ", name)[:80] or "Untitled"


def export_dir(profile: CreatorProfile, project_title: str) -> Path | None:
    if not profile.export_folder.strip():
        return None
    raw = profile.export_folder.replace("<project>", safe_name(project_title)).replace("{project}", safe_name(project_title))
    return Path(raw).expanduser()


def export_metadata(profile: CreatorProfile, *, title: str, series: str, description: str,
                    now: datetime | None = None) -> dict[str, str]:
    """MP4 tags for an export (ffmpeg -metadata keys; mov muxer writes them as iTunes-style atoms)."""
    now = now or datetime.now(timezone.utc)
    return {
        "title": title,
        "artist": profile.creator_name,
        "album_artist": profile.creator_name,
        "album": series.strip() or profile.channel_name,
        "copyright": profile.copyright.replace("{year}", str(now.year)),
        "description": description,
        "comment": description,
        "date": str(now.year),
        "creation_time": now.strftime("%Y-%m-%dT%H:%M:%S.000000Z"),
        "encoder": APP_NAME,
    }
