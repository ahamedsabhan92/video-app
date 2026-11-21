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
        assert "hello" in client.get("/api/health").json()["job_kinds"]
        with client.websocket_connect("/api/ws/events") as ws:
            job = client.post("/api/jobs", json={"kind": "hello", "params": {"steps": 3, "delay": 0.01}}).json()
            statuses = []
            while True:
                ev = ws.receive_json()
                if ev["job"]["id"] == job["id"]:
                    statuses.append(ev["job"]["status"])
                    if ev["job"]["status"] == "done":
                        break
        assert "running" in statuses
        assert client.get(f"/api/jobs/{job['id']}").json()["result"]["greeting"] == "hello, world"


def test_unknown_job_kind(tmp_path):
    client, _ = _client(tmp_path)
    with client:
        assert client.post("/api/jobs", json={"kind": "nope"}).status_code == 400


def test_projects_are_folders(tmp_path):
    client, s = _client(tmp_path)
    with client:
        p = client.post("/api/projects", json={"title": "Ella's School Morning"}).json()
        assert p["id"].startswith("ella-s-school-morning-")
        assert (s.projects_dir / p["id"] / "project.json").exists()
        assert (s.projects_dir / p["id"] / "assets").is_dir()
        assert client.get(f"/api/projects/{p['id']}").json()["title"] == "Ella's School Morning"
        assert len(client.get("/api/projects").json()) == 1
        assert client.get("/api/projects/../etc").status_code == 404


def test_websocket_client_leaving_does_not_block_shutdown(tmp_path):
    """Regression: a disconnected /ws/events client used to hang engine shutdown/reload."""
    import threading
    client, _ = _client(tmp_path)
    done = threading.Event()

    def run():
        with client:
            with client.websocket_connect("/api/ws/events"):
                pass  # connect and leave
            client.post("/api/jobs", json={"kind": "hello", "params": {"steps": 1, "delay": 0}})
        done.set()  # exiting `with client` runs app shutdown

    threading.Thread(target=run, daemon=True).start()
    assert done.wait(5), "engine shutdown hung after a websocket client disconnected"
    assert len(client.app.state.bus._subs) == 0


def test_project_settings_patch(tmp_path):
    client, _ = _client(tmp_path)
    with client:
        p = client.post("/api/projects", json={"title": "Shorts", "aspect": "9:16", "second_aspect": None}).json()
        assert p["aspect"] == "9:16" and p["second_aspect"] is None
        r = client.patch(f"/api/projects/{p['id']}", json={"second_aspect": "16:9", "second_aspect_mode": "native"})
        assert r.status_code == 200 and r.json()["second_aspect_mode"] == "native"
        assert client.patch(f"/api/projects/{p['id']}", json={"second_aspect_mode": "magic"}).status_code == 422
        assert client.patch(f"/api/projects/{p['id']}", json={"aspect": "21:9"}).status_code == 422
        assert client.patch(f"/api/projects/{p['id']}", json={"data": {}}).status_code == 422
        d = client.post("/api/projects", json={"title": "Default"}).json()
        assert d["second_aspect"] == "9:16" and d["second_aspect_mode"] == "blurpad"


def test_ui_shell_does_not_swallow_api_404s(tmp_path):
    client, _ = _client(tmp_path)
    with client:
        r = client.get("/api/does-not-exist")
        assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")
