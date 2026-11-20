"""'hello' job: proves the queue end to end (progress, cancel, per-step resume)."""
from __future__ import annotations

import time

from app.queue import JobContext, register


@register("hello")
def hello(ctx: JobContext) -> dict:
    steps = int(ctx.params.get("steps", 5))
    delay = float(ctx.params.get("delay", 0.2))
    fail_at = ctx.params.get("fail_at")  # test hook: fail once at this step
    ran = []
    for i in range(steps):
        with ctx.step(f"step-{i}") as todo:
            if todo:
                if fail_at == i and not ctx.params.get("_failed_once"):
                    ctx.params["_failed_once"] = True
                    raise RuntimeError(f"simulated failure at step {i}")
                time.sleep(delay)
                ran.append(i)
        ctx.progress((i + 1) / steps, f"step {i + 1}/{steps}")
    name = ctx.params.get("name", "world")
    return {"greeting": f"hello, {name}", "ran_steps": ran}
