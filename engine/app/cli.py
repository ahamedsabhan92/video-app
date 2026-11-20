"""Tiny CLI client for a running engine.

    python -m app.cli hello
    python -m app.cli script "Ella's school morning routine for kids" --seconds 60 --style 3d-cartoon
"""
from __future__ import annotations

import argparse
import asyncio
import json

import httpx
import websockets

TERMINAL = ("done", "failed", "cancelled")


async def run_job(url: str, body: dict, quiet: bool = False) -> dict:
    """Submit a job and stream its progress until it finishes. Returns the final job."""
    ws_url = url.replace("http", "ws", 1) + "/ws/events"
    async with websockets.connect(ws_url, max_size=None) as ws:
        r = httpx.post(f"{url}/jobs", json=body)
        r.raise_for_status()
        job = r.json()
        print(f"submitted {body['kind']} job {job['id']}")
        last = None
        async for raw in ws:
            j = json.loads(raw)["job"]
            key = (j["status"], round(j["progress"], 2), j["message"])
            if j["id"] != job["id"] or key == last:
                continue
            last = key
            if not quiet:
                print(f"  {j['status']:<9} {j['progress'] * 100:5.1f}%  {j['message']}", flush=True)
            if j["status"] in TERMINAL:
                return j
    raise RuntimeError("event stream closed")


async def hello(url: str, steps: int) -> int:
    j = await run_job(url, {"kind": "hello", "params": {"steps": steps, "delay": 0.4}})
    print("result:", j["result"] or j["error"])
    return 0 if j["status"] == "done" else 1


async def script(url: str, idea: str, seconds: int, style: str, aspect: str, think: bool, review: bool) -> int:
    p = httpx.post(f"{url}/projects", json={"title": idea[:60], "idea": idea, "style": style, "aspect": aspect})
    p.raise_for_status()
    pid = p.json()["id"]
    print(f"project {pid}")
    j = await run_job(url, {"kind": "script", "project_id": pid, "params": {"seconds": seconds, "think": think, "review": review}})
    if j["status"] != "done":
        print("error:", j["error"])
        return 1
    proj = httpx.get(f"{url}/projects/{pid}").json()
    print(json.dumps(proj["data"]["script"], indent=2, ensure_ascii=False))
    print("summary:", j["result"])
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="app.cli")
    ap.add_argument("--url", default="http://127.0.0.1:8765")
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("hello")
    h.add_argument("--steps", type=int, default=5)
    s = sub.add_parser("script")
    s.add_argument("idea")
    s.add_argument("--seconds", type=int, default=60)
    s.add_argument("--style", default="3d-cartoon")
    s.add_argument("--aspect", default="16:9")
    s.add_argument("--think", action="store_true", help="qwen3 thinking mode (slower)")
    s.add_argument("--no-review", dest="review", action="store_false", help="skip the LLM review pass")
    a = ap.parse_args()
    if a.cmd == "hello":
        return asyncio.run(hello(a.url, a.steps))
    return asyncio.run(script(a.url, a.idea, a.seconds, a.style, a.aspect, a.think, a.review))


if __name__ == "__main__":
    raise SystemExit(main())
