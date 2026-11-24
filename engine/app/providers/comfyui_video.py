"""VideoProvider: Wan 2.2 TI2V-5B (GGUF Q8, Apache-2.0) through ComfyUI + ComfyUI-GGUF. Image → clip, 24 fps.

Same server, queue, cancel and /free as the image provider. Settings (size, frames, steps) are explicit so
the animate job can step down when a clip is too slow or needs too much memory.
"""
from __future__ import annotations

import hashlib
import random
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx
from websockets.sync.client import connect

from app.providers.base import Progress, VideoProvider
from app.providers.comfyui_image import ComfyUIError, ComfyUIImageProvider

FPS = 24
VIDEO_MODELS = {"unet": "Wan2.2-TI2V-5B-Q8_0.gguf", "text_encoder": "umt5-xxl-encoder-Q5_K_M.gguf", "vae": "wan2.2_vae.safetensors"}
NEGATIVE = ("static, frozen, blurry details, overexposed, subtitles, text, watermark, worst quality, low quality, JPEG artifacts, "
            "ugly, deformed, disfigured, extra limbs, extra legs, extra wings, two tails, fused limbs, morphing, flickering, "
            "new characters appearing, scene change, cut, walking backwards")


@dataclass(frozen=True)
class VideoSettings:
    width: int
    height: int
    frames: int           # 4k+1
    steps: int = 20
    cfg: float = 5.0
    shift: float = 8.0

    @property
    def seconds(self) -> float:
        return self.frames / FPS

    def label(self) -> str:
        return f"{self.width}x{self.height}, {self.frames} frames, {self.steps} steps"

    def dict(self) -> dict:
        return asdict(self)


def prompt_key(text: str, models: dict[str, str] = VIDEO_MODELS) -> str:
    return hashlib.sha1(f"{models['text_encoder']}\n{text}".encode()).hexdigest()


def encode_workflow(prompts: list[str], models: dict[str, str] = VIDEO_MODELS) -> dict[str, Any]:
    """Only the text encoder: every prompt → a small file. Run on its own, because together with the video
    model it peaked at 10.6-13.5 GB; afterwards ComfyUI frees it and the clips load the files."""
    g: dict[str, Any] = {"2": {"class_type": "CLIPLoaderGGUF", "inputs": {"clip_name": models["text_encoder"], "type": "wan"}}}
    for i, p in enumerate(prompts):
        g[str(100 + 2 * i)] = {"class_type": "CLIPTextEncode", "inputs": {"text": p, "clip": ["2", 0]}}
        g[str(101 + 2 * i)] = {"class_type": "SeekaasSaveConditioning", "inputs": {"conditioning": [str(100 + 2 * i), 0],
                                                                                   "key": prompt_key(p, models)}}
    return g


def wan_workflow(prompt: str, image: str, *, s: VideoSettings, seed: int, prefix: str = "video_app_clip",
                 models: dict[str, str] = VIDEO_MODELS) -> dict[str, Any]:
    """Official Wan 2.2 5B image→video graph with GGUF loaders, prompts already encoded (encode_workflow),
    tiled decode and a visually lossless file."""
    return {
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": models["unet"]}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": models["vae"]}},
        "4": {"class_type": "SeekaasLoadConditioning", "inputs": {"key": prompt_key(prompt, models)}},
        "5": {"class_type": "SeekaasLoadConditioning", "inputs": {"key": prompt_key(NEGATIVE, models)}},
        "6": {"class_type": "LoadImage", "inputs": {"image": image}},
        "7": {"class_type": "Wan22ImageToVideoLatent", "inputs": {"vae": ["3", 0], "width": s.width, "height": s.height,
                                                                  "length": s.frames, "batch_size": 1, "start_image": ["6", 0]}},
        "8": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": s.shift}},
        "9": {"class_type": "KSampler", "inputs": {"model": ["8", 0], "positive": ["4", 0], "negative": ["5", 0],
                                                   "latent_image": ["7", 0], "seed": seed, "steps": s.steps, "cfg": s.cfg,
                                                   "sampler_name": "uni_pc", "scheduler": "simple", "denoise": 1.0}},
        "12": {"class_type": "VAEDecodeTiled", "inputs": {"samples": ["9", 0], "vae": ["3", 0], "tile_size": 384, "overlap": 64,
                                                          # the Wan decoder goes frame by frame anyway: splitting time saved nothing and made
                                                          # the picture pulse every 12 frames (4096 = don't split)
                                                          "temporal_size": 4096, "temporal_overlap": 8}},
        "13": {"class_type": "CreateVideo", "inputs": {"images": ["12", 0], "fps": float(FPS)}},
        "14": {"class_type": "SaveVideo", "inputs": {"video": ["13", 0], "filename_prefix": f"video/{prefix}", "format": "mp4",
                                                     "codec": "h264"}},
    }


class ComfyUIVideoProvider(VideoProvider):
    name = "comfyui-wan2.2-ti2v-5b"

    def __init__(self, url: str = "http://127.0.0.1:8188", timeout_s: float = 3600, before_run=None):
        self._c = ComfyUIImageProvider(url, timeout_s=timeout_s, before_run=before_run)
        self.url = self._c.url
        self._encoded: set[str] = set()

    def _run(self, graph: dict[str, Any], progress: Progress | None) -> str:
        client_id = uuid.uuid4().hex
        with connect(self.url.replace("http", "ws", 1) + f"/ws?clientId={client_id}", open_timeout=5, max_size=None) as ws:
            r = httpx.post(f"{self.url}/prompt", json={"prompt": graph, "client_id": client_id}, timeout=30)
            if r.status_code != 200:
                raise ComfyUIError(f"ComfyUI rejected the video workflow: {r.text[:500]}")
            pid = r.json()["prompt_id"]
            try:
                self._c._follow(ws, pid, progress)
            except BaseException:
                try:
                    httpx.post(f"{self.url}/interrupt", timeout=5)
                except httpx.HTTPError:
                    pass
                raise
        return pid

    def prepare(self, motion_prompts: list[str], progress: Progress | None = None) -> None:
        todo = [p for p in dict.fromkeys([NEGATIVE, *motion_prompts]) if prompt_key(p) not in self._encoded]
        if not todo:
            return
        self._c._check_up()
        self._run(encode_workflow(todo), progress)
        self._encoded |= {prompt_key(p) for p in todo}
        self._c.unload()                 # the encoder must be gone before the video model loads

    def animate(self, image: Path, out: Path, *, motion_prompt: str, seconds: float, seed: int | None = None,
                progress: Progress | None = None, settings: VideoSettings | None = None,
                timeout_s: float | None = None) -> Path:
        s = settings or VideoSettings(832, 480, 49)
        if timeout_s:
            self._c.timeout_s = timeout_s
        self.prepare([motion_prompt], progress)
        self._c._check_up()
        name = self._c._upload(image)
        seed = random.randrange(2**32) if seed is None else seed
        pid = self._run(wan_workflow(motion_prompt, name, s=s, seed=seed), progress)
        return self._c._download(pid, out)

    def unload(self) -> None:
        self._c.unload()
