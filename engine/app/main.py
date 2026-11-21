"""Engine entry point: `uvicorn app.main:app` (see scripts/dev)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import pipeline  # noqa: F401 — registers job handlers
from app.api.routes import router
from app.config import Settings, load_settings
from app.queue import EventBus, JobDB, JobQueue
from app.services.manager import Services, comfyui_spec, ollama_spec
from app.store.projects import ProjectStore


def create_app(settings: Settings | None = None, services: Services | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = JobDB(settings.db_path)
        bus = EventBus()
        svc = services or Services([comfyui_spec(settings.comfyui_dir, settings.comfyui_url),
                                    ollama_spec(settings.ollama_url)], settings.data_dir)
        queue = JobQueue(db, bus, settings, svc)
        app.state.services = svc
        app.state.settings = settings
        app.state.projects = ProjectStore(settings.projects_dir)
        app.state.bus, app.state.queue = bus, queue
        await queue.start()
        yield
        await queue.stop()
        svc.stop_all()  # only stops servers the engine itself started
        db.close()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    _file_log(settings.data_dir / "engine.log")
    app = FastAPI(title="AI Video Studio engine", version="0.1.0", lifespan=lifespan)
    app.include_router(router, prefix="/api")
    _mount_ui(app)
    return app


def _file_log(path) -> None:
    """Also log to data/engine.log (rotating), so errors are findable without a terminal."""
    from logging.handlers import RotatingFileHandler
    root = logging.getLogger()
    if any(isinstance(h, RotatingFileHandler) and h.baseFilename == str(path) for h in root.handlers):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    h = RotatingFileHandler(path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(h)


def _mount_ui(app: FastAPI) -> None:
    """Serve the built UI (ui/dist) at / when it exists; client-side routes fall back to index.html."""
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    from app.config import REPO_ROOT
    dist = REPO_ROOT / "ui" / "dist"
    if not (dist / "index.html").exists():
        return
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="ui-assets")

    @app.get("/{path:path}", include_in_schema=False)
    def ui(path: str):
        if path == "api" or path.startswith("api/"):  # unknown API route: real 404, not the app shell
            from fastapi import HTTPException
            raise HTTPException(404, "not found")
        f = (dist / path).resolve()
        if path and f.is_file() and f.is_relative_to(dist.resolve()):
            return FileResponse(f)
        return FileResponse(dist / "index.html")


app = create_app()
