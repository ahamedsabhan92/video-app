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


@pytest.fixture
def tone_wav(tmp_path):
    """A short real audio file (2 s sine) made with ffmpeg."""
    import subprocess
    out = tmp_path / "tone.flac"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    "-ar", "44100", str(out)], check=True)
    return out
