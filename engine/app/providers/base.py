"""Provider interfaces. Every generation step goes through one of these.

Local adapters come first (M1+); cloud adapters are optional later. Implementations are
called from inside a job handler and receive the JobContext-style `progress` callback so
long generations can report progress and be cancelled (the callback raises on cancel).
Heavy providers must free their model in `unload()`; the queue calls it after each job.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Any

Progress = Callable[[float, str], None]


class Provider(ABC):
    name: str = "base"

    def unload(self) -> None:  # noqa: B027 — optional hook
        """Release model memory. Default: nothing loaded."""


class ScriptProvider(Provider):
    @abstractmethod
    def generate(self, prompt: str, *, system: str | None = None,
                 json_schema: dict[str, Any] | None = None, progress: Progress | None = None) -> str: ...


class ImageProvider(Provider):
    @abstractmethod
    def generate(self, prompt: str, out: Path, *, width: int, height: int,
                 references: list[Path] | None = None, seed: int | None = None,
                 progress: Progress | None = None) -> Path: ...


class VideoProvider(Provider):
    @abstractmethod
    def animate(self, image: Path, out: Path, *, motion_prompt: str, seconds: float,
                seed: int | None = None, progress: Progress | None = None) -> Path: ...


class VoiceProvider(Provider):
    @abstractmethod
    def speak(self, text: str, out: Path, *, voice: str, language: str = "en",
              progress: Progress | None = None) -> Path: ...


class MusicProvider(Provider):
    @abstractmethod
    def compose(self, prompt: str, out: Path, *, seconds: float,
                progress: Progress | None = None) -> Path: ...


class SfxProvider(Provider):
    @abstractmethod
    def effect(self, prompt: str, out: Path, *, seconds: float,
               progress: Progress | None = None) -> Path: ...
