"""Job records, persisted in SQLite (metadata only — media stays on disk)."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


TERMINAL = {JobStatus.done, JobStatus.failed, JobStatus.cancelled}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Job(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    kind: str
    project_id: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    status: JobStatus = JobStatus.queued
    progress: float = 0.0          # 0..1
    message: str = ""
    steps_done: list[str] = Field(default_factory=list)  # for per-shot resume
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


_COLS = ["id", "kind", "project_id", "params", "status", "progress", "message",
         "steps_done", "result", "error", "created_at", "updated_at"]
_JSON = {"params", "steps_done", "result"}


class JobDB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, kind TEXT, project_id TEXT,"
            " params TEXT, status TEXT, progress REAL, message TEXT, steps_done TEXT,"
            " result TEXT, error TEXT, created_at TEXT, updated_at TEXT)"
        )
        self._conn.commit()

    def save(self, job: Job) -> None:
        job.updated_at = _now()
        d = job.model_dump(mode="json")
        vals = [json.dumps(d[c]) if c in _JSON else d[c] for c in _COLS]
        with self._lock:
            self._conn.execute(
                f"INSERT OR REPLACE INTO jobs ({','.join(_COLS)}) VALUES ({','.join('?' * len(_COLS))})",
                vals,
            )
            self._conn.commit()

    def _row(self, row: tuple) -> Job:
        d = dict(zip(_COLS, row))
        for c in _JSON:
            d[c] = json.loads(d[c]) if d[c] is not None else None
        return Job.model_validate(d)

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            row = self._conn.execute(f"SELECT {','.join(_COLS)} FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._row(row) if row else None

    def list(self, status: JobStatus | None = None, limit: int = 200) -> list[Job]:
        q = f"SELECT {','.join(_COLS)} FROM jobs"
        args: tuple = ()
        if status:
            q += " WHERE status=?"
            args = (status.value,)
        q += " ORDER BY created_at DESC LIMIT ?"
        with self._lock:
            rows = self._conn.execute(q, (*args, limit)).fetchall()
        return [self._row(r) for r in rows]

    def close(self) -> None:
        self._conn.close()
