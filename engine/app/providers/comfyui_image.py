"""ImageProvider backed by a local ComfyUI server, running FLUX.2 [klein] 4B (distilled, fp8).

We build an API-format workflow, queue it with POST /prompt, follow progress on ComfyUI's
WebSocket, then download the result via /history + /view. Cancelling (our progress callback
raises) sends POST /interrupt. unload() asks ComfyUI to free its models (POST /free).
"""
from __future__ import annotations

import json
import random
import uuid
from pathlib import Path
from typing import Any

import httpx
from websockets.sync.client import connect

from app.providers.base import ImageProvider, Progress

DEFAULT_MODELS = {
    "unet": "flux-2-klein-4b-fp8.safetensors",
    "text_encoder": "qwen_3_4b_fp8_mixed.safetensors",  # fp8: ~30% faster, half the memory, same look
    "vae": "flux2-vae.safetensors",
}


class ComfyUIError(RuntimeError):
    pass


def flux2_klein_workflow(prompt: str, *, width: int, height: int, seed: int, steps: int = 4,
                         prefix: str = "video_app", models: dict[str, str] = DEFAULT_MODELS) -> dict[str, Any]:
    """Text-to-image graph matching ComfyUI's official FLUX.2 klein 4B distilled template."""
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": models["unet"], "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": models["text_encoder"], "type": "flux2"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": models["vae"]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        "5": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["4", 0]}},
        "6": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "7": {"class_type": "Flux2Scheduler", "inputs": {"steps": steps, "width": width, "height": height}},
        "8": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "9": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "10": {"class_type": "CFGGuider", "inputs": {"model": ["1", 0], "positive": ["4", 0],
                                                      "negative": ["5", 0], "cfg": 1.0}},
        "11": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["9", 0], "guider": ["10", 0],
                                                                 "sampler": ["8", 0], "sigmas": ["7", 0],
                                                                 "latent_image": ["6", 0]}},
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["3", 0]}},
        "13": {"class_type": "SaveImage", "inputs": {"images": ["12", 0], "filename_prefix": prefix}},
    }


class ComfyUIImageProvider(ImageProvider):
    name = "comfyui-flux2-klein"

    def __init__(self, url: str = "http://127.0.0.1:8188", steps: int = 4, timeout_s: float = 900):
        self.url = url.rstrip("/")
        self.steps, self.timeout_s = steps, timeout_s

    def _check_up(self) -> None:
        try:
            httpx.get(f"{self.url}/system_stats", timeout=3).raise_for_status()
        except httpx.HTTPError as e:
            raise ComfyUIError(f"ComfyUI is not running at {self.url} (start it: scripts/dev comfyui)") from e

    def generate(self, prompt: str, out: Path, *, width: int, height: int,
                 references: list[Path] | None = None, seed: int | None = None,
                 progress: Progress | None = None) -> Path:
        if references:
            raise NotImplementedError("reference images arrive in M2 (ReferenceLatent)")
        self._check_up()
        seed = random.randrange(2**32) if seed is None else seed
        client_id = uuid.uuid4().hex
        graph = flux2_klein_workflow(prompt, width=width, height=height, seed=seed, steps=self.steps)
        ws_url = self.url.replace("http", "ws", 1) + f"/ws?clientId={client_id}"
        with connect(ws_url, open_timeout=5, max_size=None) as ws:
            r = httpx.post(f"{self.url}/prompt", json={"prompt": graph, "client_id": client_id}, timeout=30)
            if r.status_code != 200:
                raise ComfyUIError(f"ComfyUI rejected the workflow: {r.text[:500]}")
            prompt_id = r.json()["prompt_id"]
            try:
                self._follow(ws, prompt_id, progress)
            except BaseException:
                try:
                    httpx.post(f"{self.url}/interrupt", timeout=5)
                except httpx.HTTPError:
                    pass  # server already gone; keep the original error  # cancel or error: stop the GPU work
                raise
        return self._download(prompt_id, out)

    def _follow(self, ws, prompt_id: str, progress: Progress | None) -> None:
        import time
        deadline = time.monotonic() + self.timeout_s
        stage = "loading models"
        while True:
            if time.monotonic() > deadline:
                raise ComfyUIError("timed out waiting for ComfyUI")
            try:
                raw = ws.recv(timeout=1.0)
            except TimeoutError:
                if progress:
                    progress(None, stage)  # no new fraction; still lets the job check for cancel
                continue
            if isinstance(raw, bytes):
                continue  # binary preview frames
            msg = json.loads(raw)
            t, data = msg.get("type"), msg.get("data", {})
            if data.get("prompt_id") not in (None, prompt_id):
                continue
            if t == "progress" and progress:
                stage = "sampling"
                progress(0.1 + 0.85 * data["value"] / max(1, data["max"]), f"sampling {data['value']}/{data['max']}")
            elif t == "executing" and data.get("node") is None:
                return  # finished
            elif t == "executing" and progress:
                stage = {"1": "loading image model", "2": "loading text encoder", "4": "reading prompt",
                         "12": "decoding"}.get(str(data.get("node")), stage)
                progress(None, stage)
            elif t == "execution_error":
                raise ComfyUIError(f"{data.get('exception_type')}: {data.get('exception_message', '')[:400]}")
            elif t == "execution_interrupted":
                raise ComfyUIError("ComfyUI run was interrupted")

    def _download(self, prompt_id: str, out: Path) -> Path:
        hist = httpx.get(f"{self.url}/history/{prompt_id}", timeout=30).json()[prompt_id]
        images = [img for node in hist["outputs"].values() for key in ("images", "audio")
                  for img in node.get(key, [])]
        if not images:
            raise ComfyUIError("ComfyUI finished without an output file")
        img = images[0]
        r = httpx.get(f"{self.url}/view", params={"filename": img["filename"], "subfolder": img["subfolder"],
                                                   "type": img["type"]}, timeout=60)
        r.raise_for_status()
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(out.suffix + ".part")
        tmp.write_bytes(r.content)
        tmp.replace(out)
        return out

    def unload(self) -> None:
        try:
            httpx.post(f"{self.url}/free", json={"unload_models": True, "free_memory": True}, timeout=10)
        except httpx.HTTPError:
            pass
