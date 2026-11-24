"""Paths and settings. Override with env vars (VIDEO_APP_*)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    projects_dir: Path
    models_dir: Path
    data_dir: Path
    comfyui_dir: Path
    ollama_url: str
    comfyui_url: str

    @property
    def characters_dir(self) -> Path:
        """Character library (shared by all projects), next to projects/."""
        return Path(os.environ.get("VIDEO_APP_CHARACTERS", self.projects_dir.parent / "characters"))

    @property
    def brand_dir(self) -> Path:
        """Logos (and fonts/) for the cards and the watermark; chosen in Settings."""
        return Path(os.environ.get("VIDEO_APP_BRAND", REPO_ROOT / "brand"))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "engine.db"


def load_settings() -> Settings:
    env = os.environ.get
    return Settings(
        projects_dir=Path(env("VIDEO_APP_PROJECTS", REPO_ROOT / "projects")),
        models_dir=Path(env("VIDEO_APP_MODELS", REPO_ROOT / "models")),
        data_dir=Path(env("VIDEO_APP_DATA", REPO_ROOT / "data")),
        comfyui_dir=Path(env("VIDEO_APP_COMFYUI", REPO_ROOT / "vendor" / "ComfyUI")),
        ollama_url=env("VIDEO_APP_OLLAMA_URL", "http://127.0.0.1:11434"),
        comfyui_url=env("VIDEO_APP_COMFYUI_URL", "http://127.0.0.1:8188"),
    )
