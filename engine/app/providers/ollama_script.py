"""ScriptProvider backed by a local Ollama server (structured JSON output, streamed)."""
from __future__ import annotations

import json
from typing import Any

import httpx

from app.providers.base import Progress, ScriptProvider


class OllamaScriptProvider(ScriptProvider):
    name = "ollama"

    def __init__(self, url: str = "http://127.0.0.1:11434", model: str = "qwen3:8b", *,
                 think: bool = False, temperature: float = 0.7, num_ctx: int = 16384):
        self.url, self.model, self.think = url.rstrip("/"), model, think
        self.options = {"temperature": temperature, "num_ctx": num_ctx}
        # rough token budget per call, only used for the progress estimate
        self.expected_tokens = 3500 if think else 1500

    def generate(self, prompt: str, *, system: str | None = None,
                 json_schema: dict[str, Any] | None = None, progress: Progress | None = None) -> str:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": ([{"role": "system", "content": system}] if system else [])
                        + [{"role": "user", "content": prompt}],
            "stream": True,
            "think": self.think,      # qwen3 reasoning trace (slower, usually more coherent)
            "options": self.options,
            "keep_alive": "2m",       # stay loaded between draft/review calls of one job
        }
        if json_schema:
            body["format"] = json_schema
        out, tokens = [], 0
        with httpx.stream("POST", f"{self.url}/api/chat", json=body, timeout=httpx.Timeout(900, connect=5)) as r:
            if r.status_code != 200:
                r.read()
                raise RuntimeError(f"ollama {r.status_code}: {r.text[:300]}")
            for line in r.iter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                if "error" in chunk:
                    raise RuntimeError(f"ollama: {chunk['error']}")
                msg = chunk.get("message", {})
                thinking = bool(msg.get("thinking"))
                out.append(msg.get("content", ""))
                tokens += 1
                if progress and tokens % 25 == 0:
                    progress(min(0.95, tokens / self.expected_tokens),
                             f"{'thinking' if thinking else 'writing'}… {tokens} tokens")
                if chunk.get("done"):
                    break
        return "".join(out)

    def unload(self) -> None:
        """Free the model's memory now (keep_alive=0) so the next job has the RAM."""
        try:
            httpx.post(f"{self.url}/api/generate", json={"model": self.model, "keep_alive": 0}, timeout=10)
        except httpx.HTTPError:
            pass
