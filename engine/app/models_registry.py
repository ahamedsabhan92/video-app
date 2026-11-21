"""What each capability needs, and whether it's ready (installed / downloading / missing).

Used by GET /api/models (UI disables buttons + shows progress) and by jobs before they start,
so a missing model gives a clear message instead of a stack trace.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import Settings


@dataclass(frozen=True)
class ModelFile:
    label: str
    rel: str      # path under models/
    size: int     # expected bytes (for download progress)


CAPABILITIES: dict[str, dict] = {
    "script": {"label": "Script writer (Qwen3 8B)", "ollama": "qwen3:8b", "install": "scripts/install-models script"},
    "images": {"label": "Image model (FLUX.2 klein 4B)", "install": "scripts/install-models images", "files": [
        ModelFile("image transformer", "diffusion_models/flux-2-klein-4b-fp8.safetensors", 4_070_624_520),
        ModelFile("text encoder", "text_encoders/qwen_3_4b_fp8_mixed.safetensors", 5_631_994_051),
        ModelFile("VAE", "vae/flux2-vae.safetensors", 336_213_556),
    ]},
    "music": {"label": "Music model (Stable Audio 3 Small)", "install": "scripts/install-models music", "files": [
        ModelFile("music model", "checkpoints/stable_audio_3_small_music.safetensors", 2_270_384_940),
        ModelFile("text encoder", "text_encoders/t5gemma_b_b_ul2.safetensors", 1_187_264_003),
    ]},
    "voice": {"label": "Narration voice (Kokoro)", "install": "scripts/install-models voice", "files": [
        ModelFile("voice model", "kokoro/kokoro-v1.0.onnx", 325_532_387),
        ModelFile("voices", "kokoro/voices-v1.0.bin", 28_214_398),
    ]},
}


class ModelNotReady(RuntimeError):
    """Raised by jobs when a capability's model isn't installed yet. Message is user-facing."""


def _gb(n: int) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e8 else f"{n / 1e6:.0f} MB"


def status(settings: Settings, name: str) -> dict:
    cap = CAPABILITIES[name]
    out = {"name": name, "label": cap["label"], "install": cap["install"]}
    if "ollama" in cap:
        try:
            tags = httpx.get(f"{settings.ollama_url}/api/tags", timeout=2).json().get("models", [])
            have = any(m["name"].startswith(cap["ollama"]) for m in tags)
            out |= {"state": "ready" if have else "missing",
                    "message": "" if have else f"{cap['label']} isn't installed. Run: {cap['install']}"}
        except httpx.HTTPError:
            # Ollama is started on demand by the script job, so "not running" still counts as ready
            # if the binary exists; the job will start it.
            ok = shutil.which("ollama") is not None
            out |= {"state": "ready" if ok else "missing",
                    "message": "" if ok else "Ollama isn't installed. Run: brew install ollama"}
        return out

    done = total = 0
    missing, downloading = [], []
    for f in cap["files"]:
        p = settings.models_dir / f.rel
        part = p.with_name(p.name + ".part")
        total += f.size
        if p.exists():
            done += f.size
        elif part.exists():
            got = part.stat().st_size
            done += got
            downloading.append(f"{f.label} {_gb(got)} of {_gb(f.size)}")
        else:
            missing.append(f.label)
    pct = round(100 * done / total) if total else 100
    if not missing and not downloading:
        state, msg = "ready", ""
    elif downloading and not missing:
        state, msg = "downloading", f"{cap['label']} is still downloading ({pct}%): {', '.join(downloading)}."
    elif downloading:
        state, msg = "downloading", (f"{cap['label']} is still downloading ({pct}%): {', '.join(downloading)}; "
                                     f"not started yet: {', '.join(missing)}.")
    else:
        state, msg = "missing", f"{cap['label']} isn't installed (missing: {', '.join(missing)}). Run: {cap['install']}"
    return out | {"state": state, "message": msg, "progress": pct}


def all_status(settings: Settings) -> list[dict]:
    return [status(settings, n) for n in CAPABILITIES]


def require(settings: Settings, name: str) -> None:
    s = status(settings, name)
    if s["state"] != "ready":
        raise ModelNotReady(s["message"])
