import pytest

from app import pipeline  # noqa: F401
from app.queue import EventBus, JobDB, JobQueue


@pytest.fixture
async def queue(tmp_path):
    db = JobDB(tmp_path / "engine.db")
    q = JobQueue(db, EventBus())
    await q.start()
    yield q
    await q.stop()
    db.close()
