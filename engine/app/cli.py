"""Tiny CLI client for a running engine. `python -m app.cli hello`."""
from __future__ import annotations

import argparse
import asyncio
import json

import httpx
import websockets


async def hello(url: str, steps: int) -> int:
    ws_url = url.replace("http", "ws", 1) + "/ws/events"
    async with websockets.connect(ws_url) as ws:
        job = httpx.post(f"{url}/jobs", json={"kind": "hello", "params": {"steps": steps, "delay": 0.4}}).json()
        print(f"submitted job {job['id']}")
        last = None
        async for raw in ws:
            j = json.loads(raw)["job"]
            key = (j["status"], j["progress"], j["message"])
            if j["id"] != job["id"] or key == last:
                continue
            last = key
            print(f"  {j['status']:<9} {j['progress'] * 100:5.1f}%  {j['message']}")
            if j["status"] in ("done", "failed", "cancelled"):
                print("result:", j["result"] or j["error"])
                return 0 if j["status"] == "done" else 1
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(prog="app.cli")
    ap.add_argument("cmd", choices=["hello"])
    ap.add_argument("--url", default="http://127.0.0.1:8765")
    ap.add_argument("--steps", type=int, default=5)
    a = ap.parse_args()
    return asyncio.run(hello(a.url, a.steps))


if __name__ == "__main__":
    raise SystemExit(main())
