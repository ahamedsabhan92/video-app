from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse
from pydantic import BaseModel, ValidationError

from app.queue import HANDLERS, JobStatus
from app.script.schema import Script
from app.styles import STYLES

router = APIRouter()


def _state(req: Request | WebSocket):
    return req.app.state


@router.get("/health")
def health(request: Request) -> dict:
    s = _state(request)
    return {"ok": True, "job_kinds": sorted(HANDLERS), "current_job": s.queue.current}


@router.get("/models")
def models(request: Request) -> list[dict]:
    """Readiness of each capability: ready / downloading (with %) / missing."""
    from app.models_registry import all_status
    return all_status(_state(request).settings)


@router.get("/settings")
def get_settings(request: Request) -> dict:
    """App settings (Settings screen). For now: the creator profile used to tag exports."""
    from app.creator import load_profile
    return {"creator": load_profile(_state(request).settings.data_dir).model_dump()}


@router.put("/settings")
def put_settings(body: dict[str, Any], request: Request) -> dict:
    from app.creator import CreatorProfile, save_profile
    try:
        profile = CreatorProfile.model_validate(body.get("creator", {}))
    except ValidationError as e:
        raise HTTPException(422, str(e)) from e
    return {"creator": save_profile(_state(request).settings.data_dir, profile).model_dump()}


@router.get("/styles")
def styles() -> list[dict]:
    return [{"key": k, "label": v["label"]} for k, v in STYLES.items()]


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
    second_aspect: str | None = "default"   # "default" = 16:9↔9:16; null = main aspect only
    second_aspect_mode: str = "blurpad"     # "blurpad" (fast) | "native" (second image per shot)


@router.get("/projects")
def list_projects(request: Request):
    return _state(request).projects.list()


@router.post("/projects", status_code=201)
def create_project(body: NewProject, request: Request):
    fields = body.model_dump()
    if fields["second_aspect"] == "default":
        fields.pop("second_aspect")
    try:
        return _state(request).projects.create(**fields)
    except ValueError as e:
        raise HTTPException(422, str(e))


class Duplicate(BaseModel):
    title: str | None = None


@router.post("/projects/{project_id}/duplicate", status_code=201)
def duplicate_project(project_id: str, body: Duplicate, request: Request):
    try:
        return _state(request).projects.duplicate(project_id, body.title)
    except (KeyError, ValueError):
        raise HTTPException(404, "project not found")


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: str, request: Request):
    st = _state(request)
    busy = [j for j in st.queue.db.list() if j.project_id == project_id
            and j.status in (JobStatus.queued, JobStatus.running)]
    if busy:
        raise HTTPException(409, f"This project has a {busy[0].kind} job {busy[0].status.value}; "
                                 "cancel it before deleting.")
    try:
        st.projects.delete(project_id)
    except (KeyError, ValueError):
        raise HTTPException(404, "project not found")


@router.patch("/projects/{project_id}")
def update_project(project_id: str, changes: dict[str, Any], request: Request):
    """Change settings: title, idea, style, aspect, second_aspect, second_aspect_mode."""
    try:
        return _state(request).projects.update_settings(project_id, changes)
    except KeyError:
        raise HTTPException(404, "project not found")
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.get("/projects/{project_id}")
def get_project(project_id: str, request: Request):
    try:
        return _state(request).projects.get(project_id)
    except (KeyError, ValueError):
        raise HTTPException(404, "project not found")


class ScriptUpdate(BaseModel):
    script: dict[str, Any]
    warnings: list[dict[str, Any]] | None = None   # e.g. with resolved warnings removed


@router.put("/projects/{project_id}/script")
def save_script(project_id: str, body: ScriptUpdate, request: Request):
    """Save a script edited in the UI (validated). Images/voice for edited shots are redone
    by their jobs, which compare the stored prompt/text with the current script."""
    store = _state(request).projects
    try:
        project = store.get(project_id)
    except (KeyError, ValueError):
        raise HTTPException(404, "project not found")
    try:
        script = Script.model_validate(body.script)
    except ValidationError as e:
        raise HTTPException(422, str(e))
    ids = [s.id for s in script.shots]
    if not all(ids) or len(set(ids)) != len(ids):
        raise HTTPException(422, "every shot needs a unique id")
    project.data["script"] = script.model_dump()
    if body.warnings is not None:
        project.data["script_warnings"] = body.warnings
    return store.save(project)


@router.get("/projects/{project_id}/files/{path:path}")
def project_file(project_id: str, path: str, request: Request):
    """Serve a file from inside the project folder (images, voice, exports)."""
    store = _state(request).projects
    try:
        root = store.assets_dir(project_id).parent.resolve()
    except ValueError:
        raise HTTPException(404, "project not found")
    f = (root / path).resolve()
    if not f.is_relative_to(root) or not f.is_file() or f.name == "project.json":
        raise HTTPException(404, "file not found")
    return FileResponse(f)


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
