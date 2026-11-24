import sys
import time

import pytest

from app.services.manager import Service, ServiceError, ServiceSpec

# A stand-in server: python's http.server serving a dir that contains a file named "health".
SERVER = [sys.executable, "-m", "http.server", "--bind", "127.0.0.1"]


def _spec(tmp_path, port, **kw):
    (tmp_path / "health").write_text("ok")
    return ServiceSpec(name="fake", health_url=f"http://127.0.0.1:{port}/health",
                       cmd=[*SERVER, str(port)], cwd=tmp_path, start_timeout_s=kw.pop("timeout", 15), **kw)


def _free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_starts_when_down_and_stops_what_it_started(tmp_path):
    port = _free_port()
    svc = Service(_spec(tmp_path, port), tmp_path / "logs")
    assert not svc.healthy()
    svc.ensure_running()
    assert svc.healthy() and svc.owned
    svc.ensure_running()          # already up: no second process
    svc.stop()
    time.sleep(0.3)
    assert not svc.healthy() and not svc.owned


def test_leaves_an_external_server_alone(tmp_path):
    import subprocess
    port = _free_port()
    (tmp_path / "health").write_text("ok")
    ext = subprocess.Popen([*SERVER, str(port)], cwd=tmp_path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        svc = Service(_spec(tmp_path, port), tmp_path / "logs")
        deadline = time.time() + 10
        while not svc.healthy() and time.time() < deadline:
            time.sleep(0.1)
        svc.ensure_running()
        assert not svc.owned       # we didn't start it…
        svc.stop()
        assert svc.healthy()       # …so we don't stop it
    finally:
        ext.terminate()


def test_crash_on_start_reports_log(tmp_path):
    spec = ServiceSpec(name="broken", health_url="http://127.0.0.1:9/health",
                       cmd=[sys.executable, "-c", "import sys; print('boom: missing model'); sys.exit(3)"],
                       start_timeout_s=10)
    with pytest.raises(ServiceError, match=r"(?s)exited with code 3.*boom: missing model"):
        Service(spec, tmp_path).ensure_running()


def test_missing_install_is_clear(tmp_path):
    spec = ServiceSpec(name="comfyui", health_url="http://127.0.0.1:9/x", cmd=[str(tmp_path / "nope" / "python")])
    with pytest.raises(ServiceError, match="not installed"):
        Service(spec, tmp_path).ensure_running()


def test_comfyui_video_mode_drops_fp32_vae_and_switches(tmp_path, monkeypatch):
    from app.services import manager as M
    spec = M.comfyui_spec(tmp_path, "http://127.0.0.1:1")
    svc = M.Service(spec, tmp_path)
    started = []

    class P:
        pid, returncode = 1, None
        def poll(self): return None
        def wait(self, t=None): return 0
    monkeypatch.setattr(M.subprocess, "Popen", lambda cmd, **k: (started.append((cmd, k["env"])), P())[1])
    monkeypatch.setattr(M.os, "killpg", lambda *a: None)
    health = iter([False, True, True, False, True, True])
    monkeypatch.setattr(M.Service, "healthy", lambda self, timeout=2: next(health))
    exe = tmp_path / ".venv/bin/python"; exe.parent.mkdir(parents=True); exe.write_text("")
    svc.ensure_running()
    svc.ensure_running()                                   # same mode: nothing new
    svc.ensure_running(mode="video")                       # other mode: restarted
    assert len(started) == 2 and "--fp32-vae" in started[0][0] and "--fp32-vae" not in started[1][0]
    assert started[1][1]["PYTORCH_MPS_HIGH_WATERMARK_RATIO"] == "0.7" and svc.mode == "video"
