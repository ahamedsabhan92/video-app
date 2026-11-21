"""Turn exceptions from job handlers into short, friendly messages for the UI.
The full error (type, message, traceback) is kept separately as error_detail and in the log."""
from __future__ import annotations

import httpx

STEP = {"script": "writing the script", "images": "generating images", "narration": "recording narration",
        "render": "rendering the video", "placeholders": "making placeholders"}


def friendly(exc: BaseException, kind: str) -> str:
    from app.models_registry import ModelNotReady
    from app.providers.comfyui_image import ComfyUIError
    from app.render.ffmpeg import FFmpegError
    from app.services.manager import ServiceError

    step = STEP.get(kind, kind)
    text = str(exc).strip()
    first = text.splitlines()[0] if text else ""
    low = text.lower()
    if isinstance(exc, ModelNotReady):
        return text
    if "out of memory" in low or isinstance(exc, MemoryError):
        return (f"Ran out of memory while {step}. Close other apps (or quit other AI tools) and try again.")
    if isinstance(exc, ServiceError):
        return f"Couldn't start a helper program while {step}: {first}"
    if isinstance(exc, ComfyUIError):
        return f"The image generator stopped with an error: {first[:300]}"
    if isinstance(exc, FFmpegError):
        return f"Video processing failed while {step}. See details for the ffmpeg message."
    if isinstance(exc, httpx.ConnectError):
        return f"Couldn't reach a local helper program while {step}. It may have crashed; try again."
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return f"Timed out while {step}. Try again; if it keeps happening, check the log."
    if isinstance(exc, (ValueError, FileNotFoundError, KeyError)) and first:
        return first[:400]   # our own validation messages are already written for people
    return f"Something went wrong while {step}."
