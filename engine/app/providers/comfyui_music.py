"""MusicProvider backed by ComfyUI running Stable Audio 3 Small (music), ~2.3 GB + T5Gemma encoder.

Licence: Stability AI Community License — commercial use allowed for creators under $1M/yr
revenue; outputs are yours. (MusicGen was the original plan, but its weights are CC-BY-NC.)
Same ComfyUI server, queue/progress/cancel and /free unload as the image provider.
Needs ComfyUI started with --fp32-vae: the audio VAE in bf16 on MPS decodes to noise
(spectral flatness 0.44, near white noise, vs 0.13 in fp32).
"""
from __future__ import annotations

import random
import uuid
from pathlib import Path
from typing import Any

import httpx
from websockets.sync.client import connect

from app.providers.base import MusicProvider, Progress
from app.providers.comfyui_image import ComfyUIError, ComfyUIImageProvider

MUSIC_MODELS = {"checkpoint": "stable_audio_3_small_music.safetensors",
                "text_encoder": "t5gemma_b_b_ul2.safetensors"}
MAX_SECONDS = 120.0  # model limit; longer videos loop the bed


def stable_audio_workflow(prompt: str, *, seconds: float, seed: int, steps: int = 8,
                          prefix: str = "video_app_music", models: dict[str, str] = MUSIC_MODELS) -> dict[str, Any]:
    """Text-to-music graph matching ComfyUI's Stable Audio 3 template (without the Qwen re-prompt;
    our script writer already gives mood/tempo/instruments)."""
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": models["checkpoint"]}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": models["text_encoder"], "type": "stable_audio"}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 0]}},
        "5": {"class_type": "EmptyLatentAudio", "inputs": {"seconds": seconds, "batch_size": 1}},
        "6": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["3", 0], "negative": ["4", 0],
                                                   "latent_image": ["5", 0], "seed": seed, "steps": steps, "cfg": 1.0,
                                                   "sampler_name": "lcm", "scheduler": "simple", "denoise": 1.0}},
        "7": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
        "8": {"class_type": "SaveAudio", "inputs": {"audio": ["7", 0], "filename_prefix": f"audio/{prefix}"}},
    }


TEMPO_BPM = {"slow": 72, "medium": 100, "fast": 124}


def music_prompt(mood: str, tempo: str, instruments: str, kids: bool = True) -> str:
    """Script music block → Stable Audio prompt. Instrumental only: it sits under narration."""
    bpm = TEMPO_BPM.get(tempo, 100)
    who = "children's video background music" if kids else "video background music"
    return (f"{mood.capitalize()} {who}, {instruments}, {bpm} BPM, {tempo} tempo, simple warm melody, "
            "soft and steady, instrumental, no vocals, clean mix, consistent energy throughout")


class ComfyUIMusicProvider(MusicProvider):
    name = "comfyui-stable-audio-3-small"

    def __init__(self, url: str = "http://127.0.0.1:8188", steps: int = 8, timeout_s: float = 900):
        self._c = ComfyUIImageProvider(url, timeout_s=timeout_s)  # shares HTTP/WS plumbing
        self.url, self.steps = self._c.url, steps

    def compose(self, prompt: str, out: Path, *, seconds: float, seed: int | None = None,
                progress: Progress | None = None) -> Path:
        self._c._check_up()
        seconds = round(min(MAX_SECONDS, max(5.0, seconds)), 1)
        seed = random.randrange(2**32) if seed is None else seed
        client_id = uuid.uuid4().hex
        graph = stable_audio_workflow(prompt, seconds=seconds, seed=seed, steps=self.steps)
        ws_url = self.url.replace("http", "ws", 1) + f"/ws?clientId={client_id}"
        with connect(ws_url, open_timeout=5, max_size=None) as ws:
            r = httpx.post(f"{self.url}/prompt", json={"prompt": graph, "client_id": client_id}, timeout=30)
            if r.status_code != 200:
                raise ComfyUIError(f"ComfyUI rejected the music workflow: {r.text[:500]}")
            prompt_id = r.json()["prompt_id"]
            try:
                self._c._follow(ws, prompt_id, progress)
            except BaseException:
                try:
                    httpx.post(f"{self.url}/interrupt", timeout=5)
                except httpx.HTTPError:
                    pass  # server already gone; keep the original error
                raise
        return self._c._download(prompt_id, out)

    def unload(self) -> None:
        self._c.unload()
