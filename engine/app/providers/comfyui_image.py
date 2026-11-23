"""ImageProvider backed by a local ComfyUI server, running FLUX.2 [klein] 4B (distilled, fp8).

We build an API-format workflow, queue it with POST /prompt, follow progress on ComfyUI's
WebSocket, then download the result via /history + /view. Cancelling (our progress callback
raises) sends POST /interrupt. unload() asks ComfyUI to free its models (POST /free).
"""
from __future__ import annotations

import json
import random
import uuid
from collections.abc import Callable
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
                         prefix: str = "video_app", models: dict[str, str] = DEFAULT_MODELS,
                         references: list[str] | None = None, ref_megapixels: float = 1.0) -> dict[str, Any]:
    """Text-to-image graph matching ComfyUI's official FLUX.2 klein 4B distilled template.

    references: image names already uploaded to ComfyUI's input folder. Each is scaled to
    ~ref_megapixels, VAE-encoded and attached with ReferenceLatent to both the prompt and the
    zeroed negative (as in the official klein image-edit template). The model then keeps the
    identity / look of what it sees ("same fox, side view", "add a red scarf")."""
    g = {
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
    pos, neg = ["4", 0], ["5", 0]
    for i, name in enumerate(references or []):
        n = 100 + 10 * i
        g[str(n)] = {"class_type": "LoadImage", "inputs": {"image": name}}
        g[str(n + 1)] = {"class_type": "ImageScaleToTotalPixels",
                         "inputs": {"image": [str(n), 0], "upscale_method": "lanczos", "megapixels": ref_megapixels,
                                    "resolution_steps": 1}}
        g[str(n + 2)] = {"class_type": "VAEEncode", "inputs": {"pixels": [str(n + 1), 0], "vae": ["3", 0]}}
        g[str(n + 3)] = {"class_type": "ReferenceLatent", "inputs": {"conditioning": pos, "latent": [str(n + 2), 0]}}
        g[str(n + 4)] = {"class_type": "ReferenceLatent", "inputs": {"conditioning": neg, "latent": [str(n + 2), 0]}}
        pos, neg = [str(n + 3), 0], [str(n + 4), 0]
    g["10"]["inputs"]["positive"], g["10"]["inputs"]["negative"] = pos, neg
    return g


class ComfyUIImageProvider(ImageProvider):
    name = "comfyui-flux2-klein"

    def __init__(self, url: str = "http://127.0.0.1:8188", steps: int = 4, timeout_s: float = 900,
                 before_run: Callable[[], None] | None = None):
        self.url = url.rstrip("/")
        self.before_run = before_run     # memory guard + start ComfyUI if needed
        self.steps, self.timeout_s = steps, timeout_s

    def _check_up(self) -> None:
        if self.before_run:
            self.before_run()
        try:
            httpx.get(f"{self.url}/system_stats", timeout=3).raise_for_status()
        except httpx.HTTPError as e:
            raise ComfyUIError(f"ComfyUI is not running at {self.url} (start it: scripts/dev comfyui)") from e

    def generate(self, prompt: str, out: Path, *, width: int, height: int,
                 references: list[Path] | None = None, seed: int | None = None,
                 progress: Progress | None = None) -> Path:
        self._check_up()
        ref_names = [self._upload(p) for p in references or []]
        seed = random.randrange(2**32) if seed is None else seed
        client_id = uuid.uuid4().hex
        graph = flux2_klein_workflow(prompt, width=width, height=height, seed=seed, steps=self.steps,
                                     references=ref_names, ref_megapixels=self.ref_megapixels(len(ref_names)))
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

    @staticmethod
    def ref_megapixels(n: int) -> float:
        """Each reference adds tokens (time + memory); share ~1.5 MP between several."""
        return 1.0 if n <= 1 else round(max(0.4, 1.5 / n), 2)

    def _upload(self, path: Path) -> str:
        """Copy a reference image into ComfyUI's input folder; returns its name there."""
        import hashlib
        data = Path(path).read_bytes()
        name = f"va_{hashlib.sha1(data).hexdigest()[:16]}{Path(path).suffix or '.png'}"
        r = httpx.post(f"{self.url}/upload/image", files={"image": (name, data, "image/png")},
                       data={"overwrite": "true"}, timeout=60)
        if r.status_code != 200:
            raise ComfyUIError(f"ComfyUI didn't accept the reference image: {r.text[:300]}")
        return r.json()["name"]

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
