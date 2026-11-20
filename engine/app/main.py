"""Engine entry point: `uvicorn app.main:app` (see scripts/dev)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import pipeline  # noqa: F401 — registers job handlers
from app.api.routes import router
from app.config import Settings, load_settings
from app.queue import EventBus, JobDB, JobQueue
from app.store.projects import ProjectStore


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = JobDB(settings.db_path)
        bus = EventBus()
        queue = JobQueue(db, bus, settings)
        app.state.settings = settings
        app.state.projects = ProjectStore(settings.projects_dir)
        app.state.bus, app.state.queue = bus, queue
        await queue.start()
        yield
        await queue.stop()
        db.close()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    app = FastAPI(title="AI Video Studio engine", version="0.1.0", lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()
