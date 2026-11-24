"""Memory guard for ComfyUI on a 16 GB Mac, plus a per-job memory log.

check(): called before every image / music (later: video) generation. ComfyUI normally sits at
~1.4 GB idle, ~8 GB between two images of one job and ~11 GB while drawing, so a reading above
LIMIT_GB *between* images means something piled up. Then, in this order:
  1. free its models (POST /free) and wait for the memory to come back;
  2. if it stays above FREED_GB, restart it (only if the engine started it).
High swap triggers step 1 too, but only while ComfyUI holds models: macOS gives swap back slowly
and other apps use it, so swap alone must not cause a restart before every image.
Nothing is freed while ComfyUI is busy with someone else's prompt.

track(): one line per job in data/memory.jsonl (start / peak / end, swap, what the guard did).
Show it with: scripts/dev memory
"""
from __future__ import annotations

import ctypes
import json
import logging
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import psutil

log = logging.getLogger("memory")

LIMIT_GB = 10.0      # ComfyUI above this between two images → free / restart
SWAP_LIMIT_GB = 8.0  # swap in use above this, while ComfyUI holds models → free
FREED_GB = 4.0       # after freeing, ComfyUI should be below this (idle is ~1.4)
MAX_LOG_BYTES = 2_000_000


@dataclass
class Reading:
    comfy_gb: float | None   # None = not running
    swap_gb: float
    free_pct: float          # memory available to apps


def footprint_gb(pid: int) -> float | None:
    """What macOS charges the process (physical footprint, incl. GPU memory and its swapped part);
    plain RSS misses Metal allocations."""
    if sys.platform != "darwin":
        try:
            return psutil.Process(pid).memory_info().rss / 1e9
        except psutil.Error:
            return None
    buf = (ctypes.c_uint64 * 64)()       # rusage_info_v4: 16-byte uuid, then u64 fields
    lib = ctypes.CDLL("/usr/lib/libproc.dylib")
    if lib.proc_pid_rusage(ctypes.c_int(pid), ctypes.c_int(4), ctypes.byref(buf)) != 0:
        return None
    return buf[2 + 7] / 1e9              # ri_phys_footprint is the 8th field after the uuid


def pid_on_port(port: int) -> int | None:
    try:
        out = subprocess.run(["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"], capture_output=True, text=True, timeout=5)
        return int(out.stdout.split()[0]) if out.stdout.split() else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


class MemoryGuard:
    def __init__(self, services: Any, comfyui_url: str, log_path: Path, *,
                 limit_gb: float = LIMIT_GB, swap_limit_gb: float = SWAP_LIMIT_GB, freed_gb: float = FREED_GB,
                 settle_s: float = 20, poll_s: float = 1.0, sample_s: float = 2.0):
        self.services, self.url, self.log_path = services, comfyui_url.rstrip("/"), log_path
        self.limit_gb, self.swap_limit_gb, self.freed_gb = limit_gb, swap_limit_gb, freed_gb
        self.settle_s, self.poll_s, self.sample_s = settle_s, poll_s, sample_s
        self._pid: int | None = None
        self._actions: list[str] = []     # what check() did during the current job
        self._checks = 0

    # ---- readings (overridden in tests) -------------------------------------------
    def read(self) -> Reading:
        return Reading(self._comfy_gb(), round(psutil.swap_memory().used / 1e9, 2),
                       round(psutil.virtual_memory().available / psutil.virtual_memory().total * 100, 1))

    def _comfy_gb(self) -> float | None:
        if self._pid is None or not psutil.pid_exists(self._pid):
            port = self.url.rsplit(":", 1)[-1]
            self._pid = pid_on_port(int(port)) if port.isdigit() else None
        gb = footprint_gb(self._pid) if self._pid else None
        if gb is None:
            self._pid = None
        return None if gb is None else round(gb, 2)

    def busy(self) -> bool:
        try:
            q = httpx.get(f"{self.url}/queue", timeout=5).json()
            return bool(q.get("queue_running") or q.get("queue_pending"))
        except (httpx.HTTPError, ValueError):
            return False

    def free(self) -> None:
        try:
            httpx.post(f"{self.url}/free", json={"unload_models": True, "free_memory": True}, timeout=10)
        except httpx.HTTPError:
            log.warning("ComfyUI did not answer /free")

    def restart(self, progress=None) -> bool:
        svc = self.services.get("comfyui")
        if not svc.owned:
            return False
        mode = getattr(svc, "mode", "")
        svc.stop()
        self._pid = None
        svc.ensure_running(progress=progress, mode=mode)
        return True

    # ---- guard ---------------------------------------------------------------------
    def check(self, progress: Callable[[float | None, str], None] | None = None) -> str:
        """Before a generation. Returns what was done: ok | freed | restarted | busy | not-owned | off."""
        r = self.read()
        self._checks += 1
        if r.comfy_gb is None:
            return "off"
        too_big = r.comfy_gb > self.limit_gb
        swapping = r.swap_gb > self.swap_limit_gb and r.comfy_gb > self.freed_gb
        if not (too_big or swapping):
            return "ok"
        why = f"ComfyUI {r.comfy_gb:.1f} GB, swap {r.swap_gb:.1f} GB"
        if self.busy():
            return self._did("busy", f"{why}; ComfyUI is busy, left alone")
        if progress:
            progress(None, "freeing memory")
        self.free()
        after = self._settle()
        if after.comfy_gb is None or after.comfy_gb <= self.freed_gb:
            return self._did("freed", f"{why} → freed models, now {after.comfy_gb} GB")
        if progress:
            progress(None, "restarting ComfyUI to free memory")
        if not self.restart(progress):
            return self._did("not-owned", f"{why}; still {after.comfy_gb} GB after freeing, but ComfyUI wasn't "
                                          "started by the engine, so it isn't restarted")
        return self._did("restarted", f"{why}; still {after.comfy_gb} GB after freeing → restarted, now {self.read().comfy_gb} GB")

    def _settle(self) -> Reading:
        """/free is handled by ComfyUI's worker loop a moment later: wait until it shows."""
        end = time.monotonic() + self.settle_s
        r = self.read()
        while time.monotonic() < end and r.comfy_gb is not None and r.comfy_gb > self.freed_gb:
            time.sleep(self.poll_s)
            r = self.read()
        return r

    def _did(self, action: str, message: str) -> str:
        log.warning("memory guard: %s", message)
        self._actions.append(message)
        return action

    # ---- per-job log -----------------------------------------------------------------
    @contextmanager
    def track(self, job: Any):
        self._actions, self._checks = [], 0
        start, t0 = self.read(), time.monotonic()
        peak = asdict(start)
        stop = threading.Event()

        def sample() -> None:
            while not stop.wait(self.sample_s):
                r = self.read()
                if r.comfy_gb is not None:
                    peak["comfy_gb"] = max(peak["comfy_gb"] or 0, r.comfy_gb)
                peak["swap_gb"] = max(peak["swap_gb"], r.swap_gb)
                peak["free_pct"] = min(peak["free_pct"], r.free_pct)

        t = threading.Thread(target=sample, daemon=True, name="memory-sampler")
        t.start()
        try:
            yield
        finally:
            stop.set()
            t.join(self.sample_s + 5)
            end = self.read()
            self._write({
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "job": job.id, "kind": job.kind,
                "status": str(getattr(job.status, "value", job.status)), "seconds": round(time.monotonic() - t0, 1),
                "comfy_start": start.comfy_gb, "comfy_peak": peak["comfy_gb"], "comfy_end": end.comfy_gb,
                "swap_start": start.swap_gb, "swap_peak": peak["swap_gb"], "swap_end": end.swap_gb,
                "free_min_pct": peak["free_pct"], "checks": self._checks, "actions": list(self._actions)})

    @contextmanager
    def watch(self):
        """Peak of one generation: {"comfy_gb", "swap_gb", "free_pct"} filled in when it ends."""
        r = self.read()
        out: dict = asdict(r)
        stop = threading.Event()

        def sample() -> None:
            while not stop.wait(self.sample_s):
                r = self.read()
                out["comfy_gb"] = max(out["comfy_gb"] or 0, r.comfy_gb or 0)
                out["swap_gb"] = max(out["swap_gb"], r.swap_gb)
                out["free_pct"] = min(out["free_pct"], r.free_pct)

        t = threading.Thread(target=sample, daemon=True, name="memory-watch")
        t.start()
        try:
            yield out
        finally:
            stop.set()
            t.join(self.sample_s + 5)

    def _write(self, row: dict) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            if self.log_path.exists() and self.log_path.stat().st_size > MAX_LOG_BYTES:
                self.log_path.replace(self.log_path.with_suffix(".old.jsonl"))
            with open(self.log_path, "a") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            log.exception("could not write the memory log")


def report(path: Path, n: int = 30) -> str:
    """The last n jobs as a table (scripts/dev memory)."""
    if not path.exists():
        return f"no jobs logged yet ({path})"
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()][-n:]
    g = lambda v: "   -" if v is None else f"{v:4.1f}"  # noqa: E731
    out = ["when (UTC)        job kind          secs | ComfyUI GB start peak  end | swap GB start peak  end | free% min | guard"]
    for r in rows:
        out.append(f"{r['at'][:16]}  {r['kind']:<16} {r['seconds']:6.0f} |            {g(r['comfy_start'])} {g(r['comfy_peak'])} "
                   f"{g(r['comfy_end'])} |         {g(r['swap_start'])} {g(r['swap_peak'])} {g(r['swap_end'])} | "
                   f"{r['free_min_pct']:8.0f}  | {'; '.join(r['actions']) or ('ok' if r['checks'] else '-')}")
    return "\n".join(out)


if __name__ == "__main__":
    from app.config import load_settings
    print(report(load_settings().data_dir / "memory.jsonl"))
