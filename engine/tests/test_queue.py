import asyncio

from app.queue import EventBus, JobDB, JobQueue, JobStatus, register


async def test_hello_runs_and_reports_progress(queue):
    events = queue.bus.subscribe()
    job = queue.submit("hello", {"steps": 4, "delay": 0.01, "name": "Ahamed"})
    done = await queue.wait(job.id)
    assert done.status == JobStatus.done
    assert done.result["greeting"] == "hello, Ahamed"
    assert done.steps_done == [f"step-{i}" for i in range(4)]
    seen = []
    while not events.empty():
        seen.append(events.get_nowait()["job"]["status"])
    assert seen[0] == "queued" and "running" in seen and seen[-1] == "done"


async def test_one_job_at_a_time(queue):
    running = []
    peak = []

    @register("probe")
    def probe(ctx):
        running.append(1)
        peak.append(len(running))
        import time; time.sleep(0.05)
        running.pop()

    ids = [queue.submit("probe").id for _ in range(3)]
    for i in ids:
        assert (await queue.wait(i)).status == JobStatus.done
    assert max(peak) == 1


async def test_cancel_running_job(queue):
    job = queue.submit("hello", {"steps": 50, "delay": 0.02})
    while queue.db.get(job.id).status != JobStatus.running:
        await asyncio.sleep(0.01)
    queue.cancel(job.id)
    assert (await queue.wait(job.id)).status == JobStatus.cancelled


async def test_cancel_queued_job(queue):
    first = queue.submit("hello", {"steps": 10, "delay": 0.02})
    second = queue.submit("hello", {"steps": 1})
    queue.cancel(second.id)
    assert (await queue.wait(second.id)).status == JobStatus.cancelled
    assert (await queue.wait(first.id)).status == JobStatus.done


async def test_retry_resumes_from_failed_step(queue):
    job = queue.submit("hello", {"steps": 5, "delay": 0.0, "fail_at": 3})
    failed = await queue.wait(job.id)
    assert failed.status == JobStatus.failed and "simulated failure" in failed.error
    assert failed.steps_done == ["step-0", "step-1", "step-2"]
    queue.retry(job.id)
    done = await queue.wait(job.id)
    assert done.status == JobStatus.done
    assert done.result["ran_steps"] == [3, 4]  # steps 0-2 were not redone


async def test_restart_requeues_interrupted_job(tmp_path):
    db = JobDB(tmp_path / "engine.db")
    q1 = JobQueue(db, EventBus())
    job = q1.submit("hello", {"steps": 3, "delay": 0.0})
    j = db.get(job.id)
    j.status, j.steps_done = JobStatus.running, ["step-0"]  # simulate crash mid-job
    db.save(j)

    q2 = JobQueue(db, EventBus())
    await q2.start()
    done = await q2.wait(job.id)
    await q2.stop()
    assert done.status == JobStatus.done
    assert done.result["ran_steps"] == [1, 2]
    db.close()
