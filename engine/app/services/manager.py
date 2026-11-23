"""Start/stop the local helper servers the engine depends on (ComfyUI, Ollama).

ensure_running(): if the service already answers its health URL, use it as is (we don't own
it). Otherwise spawn it, log to data/<name>.log, wait until healthy, and remember that we
started it so stop() can shut it down when the engine exits.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

log = logging.getLogger("services")


class ServiceError(RuntimeError):
    pass


@dataclass
class ServiceSpec:
    name: str
    health_url: str
    cmd: list[str]
    cwd: Path | None = None
    start_timeout_s: float = 120
    env: dict[str, str] | None = None


class Service:
    def __init__(self, spec: ServiceSpec, log_dir: Path):
        self.spec = spec
        self.log_path = log_dir / f"{spec.name}.log"
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    @property
    def owned(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def healthy(self, timeout: float = 2) -> bool:
        try:
            return httpx.get(self.spec.health_url, timeout=timeout).status_code == 200
        except httpx.HTTPError:
            return False

    def ensure_running(self, progress=None) -> None:
        with self._lock:  # one starter at a time (job thread vs API thread)
            if self.healthy():
                return
            if not self.owned:
                self._spawn()
            deadline = time.monotonic() + self.spec.start_timeout_s
            while time.monotonic() < deadline:
                if self.healthy():
                    log.info("%s is up (pid %s)", self.spec.name, self._proc.pid if self._proc else "?")
                    return
                if self._proc is not None and self._proc.poll() is not None:
                    raise ServiceError(f"{self.spec.name} exited with code {self._proc.returncode}; "
                                       f"see {self.log_path}:\n{self._tail()}")
                if progress:
                    progress(None, f"starting {self.spec.name}…")
                time.sleep(0.5)
            raise ServiceError(f"{self.spec.name} did not become healthy within {self.spec.start_timeout_s:.0f}s; "
                               f"see {self.log_path}")

    def _spawn(self) -> None:
        exe = Path(self.spec.cmd[0])
        if exe.is_absolute() and not exe.exists():
            raise ServiceError(f"{self.spec.name} is not installed ({exe} missing)")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        logf = open(self.log_path, "ab")
        log.info("starting %s: %s", self.spec.name, " ".join(self.spec.cmd))
        self._proc = subprocess.Popen(self.spec.cmd, cwd=self.spec.cwd, stdout=logf, stderr=subprocess.STDOUT,
                                      stdin=subprocess.DEVNULL, env={**os.environ, **(self.spec.env or {})},
                                      start_new_session=True)  # own process group: Ctrl-C on engine won't hit it

    def stop(self, timeout: float = 10) -> None:
        """Stop the service only if we started it."""
        with self._lock:
            if not self.owned:
                return
            assert self._proc is not None
            log.info("stopping %s (pid %s)", self.spec.name, self._proc.pid)
            os.killpg(self._proc.pid, signal.SIGTERM)
            try:
                self._proc.wait(timeout)
            except subprocess.TimeoutExpired:
                os.killpg(self._proc.pid, signal.SIGKILL)
                self._proc.wait(5)
            self._proc = None

    def _tail(self, n: int = 15) -> str:
        try:
            return "\n".join(self.log_path.read_text(errors="replace").splitlines()[-n:])
        except OSError:
            return ""

    def status(self) -> dict:
        return {"name": self.spec.name, "healthy": self.healthy(timeout=1), "owned": self.owned,
                "log": str(self.log_path)}


def comfyui_spec(comfyui_dir: Path, url: str) -> ServiceSpec:
    port = url.rsplit(":", 1)[-1].strip("/")
    return ServiceSpec(
        name="comfyui", health_url=f"{url.rstrip('/')}/system_stats", cwd=comfyui_dir,
        # --disable-smart-memory: free models after each run (16 GB shared memory)
        # --fp32-vae: the Stable Audio 3 VAE decodes to noise in bf16 on MPS (images: ~no cost)
        # --use-split-cross-attention: the default (sub-quadratic) sizes its work from memory that only
        #   looks free: the same image peaked at 13-18 GB (swap +6 GB, 51-499 s) vs 12 GB (54-57 s).
        cmd=[str(comfyui_dir / ".venv" / "bin" / "python"), "main.py", "--listen", "127.0.0.1",
             "--port", port, "--disable-smart-memory", "--fp32-vae", "--use-split-cross-attention"],
        start_timeout_s=180,
    )


def ollama_spec(url: str) -> ServiceSpec:
    host = url.split("://", 1)[-1].rstrip("/")
    return ServiceSpec(name="ollama", health_url=f"{url.rstrip('/')}/api/version", cmd=["ollama", "serve"],
                       env={"OLLAMA_HOST": host}, start_timeout_s=60)


class Services:
    """Registry used by the app and jobs: services.get('comfyui').ensure_running()."""
    def __init__(self, specs: list[ServiceSpec], log_dir: Path):
        self._all = {s.name: Service(s, log_dir) for s in specs}

    def get(self, name: str) -> Service:
        return self._all[name]

    def stop_all(self) -> None:
        for s in self._all.values():
            try:
                s.stop()
            except Exception:  # noqa: BLE001
                log.exception("failed to stop %s", s.spec.name)

    def status(self) -> list[dict]:
        return [s.status() for s in self._all.values()]
