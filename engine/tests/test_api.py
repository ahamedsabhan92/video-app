import time

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def _client(tmp_path):
    s = Settings(projects_dir=tmp_path / "projects", models_dir=tmp_path / "models",
                 data_dir=tmp_path / "data", comfyui_dir=tmp_path / "comfy",
                 ollama_url="http://127.0.0.1:1", comfyui_url="http://127.0.0.1:1")
    return TestClient(create_app(s)), s


def test_hello_job_via_api_and_websocket(tmp_path):
    client, _ = _client(tmp_path)
    with client:
        assert "hello" in client.get("/health").json()["job_kinds"]
        with client.websocket_connect("/ws/events") as ws:
            job = client.post("/jobs", json={"kind": "hello", "params": {"steps": 3, "delay": 0.01}}).json()
            statuses = []
            while True:
                ev = ws.receive_json()
                if ev["job"]["id"] == job["id"]:
                    statuses.append(ev["job"]["status"])
                    if ev["job"]["status"] == "done":
                        break
        assert "running" in statuses
        assert client.get(f"/jobs/{job['id']}").json()["result"]["greeting"] == "hello, world"


def test_unknown_job_kind(tmp_path):
    client, _ = _client(tmp_path)
    with client:
        assert client.post("/jobs", json={"kind": "nope"}).status_code == 400


def test_projects_are_folders(tmp_path):
    client, s = _client(tmp_path)
    with client:
        p = client.post("/projects", json={"title": "Ella's School Morning"}).json()
        assert p["id"].startswith("ella-s-school-morning-")
        assert (s.projects_dir / p["id"] / "project.json").exists()
        assert (s.projects_dir / p["id"] / "assets").is_dir()
        assert client.get(f"/projects/{p['id']}").json()["title"] == "Ella's School Morning"
        assert len(client.get("/projects").json()) == 1
        assert client.get("/projects/../etc").status_code == 404
