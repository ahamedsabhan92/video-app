from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app.queue import HANDLERS, JobStatus

router = APIRouter()


def _state(req: Request | WebSocket):
    return req.app.state


@router.get("/health")
def health(request: Request) -> dict:
    s = _state(request)
    return {"ok": True, "job_kinds": sorted(HANDLERS), "current_job": s.queue.current}


# ---- projects -----------------------------------------------------------
class NewProject(BaseModel):
    title: str
    idea: str = ""
    style: str = ""
    aspect: str = "16:9"


@router.get("/projects")
def list_projects(request: Request):
    return _state(request).projects.list()


@router.post("/projects", status_code=201)
def create_project(body: NewProject, request: Request):
    return _state(request).projects.create(**body.model_dump())


@router.get("/projects/{project_id}")
def get_project(project_id: str, request: Request):
    try:
        return _state(request).projects.get(project_id)
    except (KeyError, ValueError):
        raise HTTPException(404, "project not found")


# ---- jobs ---------------------------------------------------------------
class NewJob(BaseModel):
    kind: str
    params: dict[str, Any] = {}
    project_id: str | None = None


@router.post("/jobs", status_code=201)
def submit_job(body: NewJob, request: Request):
    try:
        return _state(request).queue.submit(body.kind, body.params, body.project_id)
    except KeyError as e:
        raise HTTPException(400, str(e))


@router.get("/jobs")
def list_jobs(request: Request, status: JobStatus | None = None):
    return _state(request).queue.db.list(status)


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request):
    job = _state(request).queue.db.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request):
    try:
        return _state(request).queue.cancel(job_id)
    except KeyError:
        raise HTTPException(404, "job not found")


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: str, request: Request):
    try:
        return _state(request).queue.retry(job_id)
    except KeyError:
        raise HTTPException(404, "job not found")
    except ValueError as e:
        raise HTTPException(409, str(e))


# ---- progress events ----------------------------------------------------
@router.websocket("/ws/events")
async def events(ws: WebSocket):
    await ws.accept()
    bus = _state(ws).bus
    q = bus.subscribe()
    try:
        while True:
            event = await q.get()
            await ws.send_json(event)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        bus.unsubscribe(q)
