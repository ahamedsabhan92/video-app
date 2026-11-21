"""Single-worker job queue: exactly one heavy job at a time (16 GB shared memory).

Handlers are plain blocking functions `fn(ctx: JobContext) -> dict | None`, run in a
worker thread. They report progress via ctx.progress(), check ctx.raise_if_cancelled()
between units of work, and use ctx.step(name) to make each unit (e.g. a shot) resumable:
completed steps are persisted and skipped when the job is retried.
"""
from __future__ import annotations

import asyncio
import gc
import logging
import sys
import threading
import time
import traceback
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any, Iterator

from .events import EventBus
from .jobs import TERMINAL, Job, JobDB, JobStatus

log = logging.getLogger("queue")

Handler = Callable[["JobContext"], dict | None]
HANDLERS: dict[str, Handler] = {}


def register(kind: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn
    return deco


class JobCancelled(Exception):
    pass


class JobContext:
    def __init__(self, job: Job, queue: "JobQueue", cancel: threading.Event):
        self.job = job
        self._queue = queue
        self._cancel = cancel
        self._last_emit = 0.0
        self.cleanups: list[Callable[[], None]] = []  # e.g. unload model

    @property
    def settings(self):
        from app.config import load_settings
        return self._queue.settings or load_settings()

    @property
    def params(self) -> dict[str, Any]:
        return self.job.params

    def raise_if_cancelled(self) -> None:
        if self._cancel.is_set():
            raise JobCancelled()

    def progress(self, fraction: float | None, message: str = "", force: bool = False) -> None:
        """fraction None = keep the current value (just update the message / check cancel)."""
        self.raise_if_cancelled()
        if fraction is not None:
            self.job.progress = max(0.0, min(1.0, fraction))
        if message:
            self.job.message = message
        now = time.monotonic()
        if force or now - self._last_emit > 0.25:  # throttle DB writes/events
            self._last_emit = now
            self._queue._persist(self.job)

    def is_done(self, step: str) -> bool:
        return step in self.job.steps_done

    @contextmanager
    def step(self, name: str) -> Iterator[bool]:
        """`with ctx.step("shot-3") as todo: if todo: ...` — skipped if already done."""
        if self.is_done(name):
            yield False
            return
        self.raise_if_cancelled()
        yield True
        self.job.steps_done.append(name)
        self._queue._persist(self.job)


class JobQueue:
    def __init__(self, db: JobDB, bus: EventBus, settings: Any = None):
        self.db = db
        self.bus = bus
        self.settings = settings  # app Settings, exposed to handlers as ctx.settings
        self._pending: asyncio.Queue[str] = asyncio.Queue()
        self._cancel_flags: dict[str, threading.Event] = {}
        self._task: asyncio.Task | None = None
        self.current: str | None = None

    # ---- lifecycle -------------------------------------------------
    async def start(self) -> None:
        self.bus.bind(asyncio.get_running_loop())
        # Resume: anything left running/queued by a previous process goes back on the queue.
        for job in sorted(self.db.list(limit=10_000), key=lambda j: j.created_at):
            if job.status in (JobStatus.running, JobStatus.queued):
                job.status = JobStatus.queued
                job.message = "resumed after restart" if job.steps_done else job.message
                self.db.save(job)
                self._pending.put_nowait(job.id)
        self._task = asyncio.create_task(self._run(), name="job-worker")

    async def stop(self) -> None:
        # A job still running is left as 'running' in the DB, so start() resumes it.
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # ---- public API ------------------------------------------------
    def submit(self, kind: str, params: dict | None = None, project_id: str | None = None) -> Job:
        if kind not in HANDLERS:
            raise KeyError(f"unknown job kind: {kind}")
        job = Job(kind=kind, params=params or {}, project_id=project_id)
        self._persist(job)
        self._pending.put_nowait(job.id)
        return job

    def cancel(self, job_id: str) -> Job:
        job = self._require(job_id)
        if job.status == JobStatus.queued:
            job.status = JobStatus.cancelled
            job.message = "cancelled"
            self._persist(job)
        elif job.status == JobStatus.running:
            self._cancel_flags[job_id].set()
        return job

    def retry(self, job_id: str) -> Job:
        """Re-queue a failed/cancelled job. Completed steps are kept → resumes where it stopped."""
        job = self._require(job_id)
        if job.status not in (JobStatus.failed, JobStatus.cancelled):
            raise ValueError(f"can only retry failed/cancelled jobs (status={job.status})")
        job.status, job.error, job.message = JobStatus.queued, None, "retrying"
        self._persist(job)
        self._pending.put_nowait(job.id)
        return job

    async def wait(self, job_id: str, timeout: float = 30) -> Job:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self._require(job_id)
            if job.status in TERMINAL:
                return job
            await asyncio.sleep(0.05)
        raise TimeoutError(job_id)

    # ---- internals -------------------------------------------------
    def _require(self, job_id: str) -> Job:
        job = self.db.get(job_id)
        if not job:
            raise KeyError(job_id)
        return job

    def _persist(self, job: Job) -> None:
        self.db.save(job)
        self.bus.publish({"type": "job", "job": job.model_dump(mode="json")})

    async def _run(self) -> None:
        while True:
            job_id = await self._pending.get()
            job = self.db.get(job_id)
            if not job or job.status != JobStatus.queued:
                continue  # cancelled while waiting, or duplicate entry
            await self._execute(job)

    async def _execute(self, job: Job) -> None:
        cancel = threading.Event()
        self._cancel_flags[job.id] = cancel
        self.current = job.id
        job.status, job.message = JobStatus.running, job.message or "started"
        self._persist(job)
        ctx = JobContext(job, self, cancel)
        try:
            result = await asyncio.to_thread(HANDLERS[job.kind], ctx)
            job.status, job.progress, job.result = JobStatus.done, 1.0, result or {}
            job.message = "done"
        except JobCancelled:
            job.status, job.message = JobStatus.cancelled, "cancelled"
        except Exception as e:  # noqa: BLE001 — report any handler failure on the job
            log.exception("job %s failed", job.id)
            job.status, job.error = JobStatus.failed, f"{type(e).__name__}: {e}"
            job.message = "failed"
            job.result = {"traceback": traceback.format_exc(limit=5)}
        finally:
            for fn in reversed(ctx.cleanups):
                try:
                    fn()
                except Exception:  # noqa: BLE001
                    log.exception("cleanup failed for job %s", job.id)
            _free_memory()
            self._cancel_flags.pop(job.id, None)
            self.current = None
            self._persist(job)


def _free_memory() -> None:
    """Unload between jobs: drop Python refs and release MPS cache if torch is loaded."""
    gc.collect()
    torch = sys.modules.get("torch")
    if torch is not None and getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        torch.mps.empty_cache()
