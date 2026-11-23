import json
import os

from app.providers.comfyui_image import ComfyUIError, ComfyUIImageProvider
from app.queue import EventBus, JobDB, JobQueue, JobStatus, register
from app.services.manager import comfyui_spec
from app.services.memory import MemoryGuard, Reading, footprint_gb, report


class FakeService:
    def __init__(self, owned=True):
        self.owned, self.calls = owned, []

    def stop(self):
        self.calls.append("stop")

    def ensure_running(self, progress=None):
        self.calls.append("start")


class FakeGuard(MemoryGuard):
    """Readings come from a list (the last one repeats); /free and /queue are recorded."""
    def __init__(self, tmp_path, readings, owned=True, busy=False):
        self.svc = FakeService(owned)
        super().__init__({"comfyui": self.svc}, "http://127.0.0.1:1", tmp_path / "memory.jsonl",
                         settle_s=0.05, poll_s=0.01, sample_s=0.01)
        self.readings, self._busy, self.freed = list(readings), busy, 0

    def read(self):
        gb, swap = self.readings.pop(0) if len(self.readings) > 1 else self.readings[0]
        return Reading(gb, swap, 50.0)

    def busy(self):
        return self._busy

    def free(self):
        self.freed += 1


def test_normal_and_not_running_do_nothing(tmp_path):
    for reading, want in (((8.2, 5.0), "ok"), ((None, 12.0), "off"), ((1.4, 12.0), "ok")):
        g = FakeGuard(tmp_path, [reading])           # last: high swap but ComfyUI holds nothing
        assert g.check() == want and g.freed == 0 and g.svc.calls == []


def test_too_big_frees_models_first(tmp_path):
    g = FakeGuard(tmp_path, [(13.0, 5.0), (9.0, 5.0), (1.5, 5.0)])
    assert g.check() == "freed" and g.freed == 1 and g.svc.calls == []


def test_high_swap_frees_models(tmp_path):
    g = FakeGuard(tmp_path, [(8.0, 9.5), (1.5, 9.0)])
    assert g.check() == "freed" and g.freed == 1


def test_restarts_when_freeing_does_not_help(tmp_path):
    g = FakeGuard(tmp_path, [(13.0, 5.0), (12.0, 5.0)])
    assert g.check() == "restarted" and g.svc.calls == ["stop", "start"]


def test_never_restarts_a_comfyui_it_did_not_start_or_that_is_busy(tmp_path):
    g = FakeGuard(tmp_path, [(13.0, 5.0)], owned=False)
    assert g.check() == "not-owned" and g.svc.calls == []
    g = FakeGuard(tmp_path, [(13.0, 5.0)], busy=True)
    assert g.check() == "busy" and g.freed == 0 and g.svc.calls == []


def test_footprint_of_this_process():
    gb = footprint_gb(os.getpid())
    assert gb is not None and 0.005 < gb < 5


def test_provider_runs_the_guard_before_each_generation(tmp_path):
    calls = []
    p = ComfyUIImageProvider("http://127.0.0.1:1", before_run=lambda: calls.append(1))
    for _ in range(2):
        try:
            p.generate("x", tmp_path / "x.png", width=64, height=64)
        except ComfyUIError:
            pass
    assert len(calls) == 2


def test_comfyui_flags():
    from pathlib import Path
    cmd = comfyui_spec(Path("/x"), "http://127.0.0.1:8188").cmd
    assert "--use-split-cross-attention" in cmd and "--fp32-vae" in cmd


async def test_each_job_is_logged_even_when_it_fails(tmp_path):
    @register("mem_ok")
    def _ok(ctx):
        ctx.comfyui_ready()
        return {}

    @register("mem_fail")
    def _fail(ctx):
        raise ValueError("boom")

    class Svcs(dict):
        def get(self, name):
            return self[name]

    g = FakeGuard(tmp_path, [(2.0, 3.0), (13.0, 3.0), (1.5, 3.0)])
    db = JobDB(tmp_path / "e.db")
    q = JobQueue(db, EventBus(), services=Svcs(comfyui=g.svc), memory=g)
    await q.start()
    a = await q.wait(q.submit("mem_ok").id)
    b = await q.wait(q.submit("mem_fail").id)
    await q.stop()
    db.close()
    assert (a.status, b.status) == (JobStatus.done, JobStatus.failed)
    rows = [json.loads(x) for x in (tmp_path / "memory.jsonl").read_text().splitlines()]
    assert [(r["kind"], r["status"]) for r in rows] == [("mem_ok", "done"), ("mem_fail", "failed")]
    assert rows[0]["comfy_start"] == 2.0 and rows[0]["checks"] == 1 and rows[1]["checks"] == 0
    assert "mem_fail" in report(tmp_path / "memory.jsonl")
