from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request, WebSocket
from pydantic import BaseModel

from app.queue import HANDLERS, JobStatus

router = APIRouter()


def _state(req: Request | WebSocket):
    return req.app.state


@router.get("/health")
def health(request: Request) -> dict:
    s = _state(request)
    return {"ok": True, "job_kinds": sorted(HANDLERS), "current_job": s.queue.current}


@router.get("/services")
def services(request: Request) -> list[dict]:
    return _state(request).services.status()


@router.post("/services/{name}/start")
def start_service(name: str, request: Request) -> dict:
    """Start a helper server now (the UI can warm ComfyUI up before the user hits Generate)."""
    from app.services.manager import ServiceError
    try:
        svc = _state(request).services.get(name)
    except KeyError:
        raise HTTPException(404, "unknown service")
    try:
        svc.ensure_running()
    except ServiceError as e:
        raise HTTPException(503, str(e))
    return svc.status()


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

    async def pump() -> None:
        while True:
            await ws.send_json(await q.get())

    async def until_disconnect() -> None:
        # We must read from the socket to notice the client leaving; otherwise this handler
        # waits on q.get() forever and blocks engine shutdown/reload.
        while True:
            if (await ws.receive())["type"] == "websocket.disconnect":
                return

    tasks = [asyncio.create_task(pump()), asyncio.create_task(until_disconnect())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except asyncio.CancelledError:
        pass
    finally:
        # Cancel without awaiting: nothing to wait for once the client is gone.
        for t in tasks:
            t.cancel()
        bus.unsubscribe(q)
