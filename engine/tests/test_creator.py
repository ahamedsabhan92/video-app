from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.creator import CreatorProfile, export_dir, export_metadata, load_profile, safe_name
from app.main import create_app


def test_defaults_and_tags():
    p = CreatorProfile()
    assert (p.creator_name, p.channel_name) == ("Ahamed Sabhan", "Tiny Tales")
    t = export_metadata(p, title="The Crow and the Fox", series="", description="A fable.",
                        now=datetime(2026, 11, 21, tzinfo=timezone.utc))
    assert t["copyright"] == "© 2026 Ahamed Sabhan. All rights reserved."
    assert t["album"] == "Tiny Tales" and t["encoder"] == "Seekaas Studio" and t["artist"] == "Ahamed Sabhan"
    assert t["creation_time"].startswith("2026-11-21T") and t["description"] == "A fable."
    assert export_metadata(p, title="x", series="Aesop", description="")["album"] == "Aesop"


def test_export_dir_and_safe_names(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert export_dir(CreatorProfile(), "Leo's Bedtime / Routine") == tmp_path / "Movies/Seekaas Studio/Leo's Bedtime Routine"
    assert export_dir(CreatorProfile(export_folder=""), "x") is None
    assert safe_name('a:b*c?') == "a b c"


def test_settings_api_roundtrip(tmp_path):
    s = Settings(projects_dir=tmp_path / "p", models_dir=tmp_path / "m", data_dir=tmp_path / "d",
                 comfyui_dir=tmp_path / "c", ollama_url="http://127.0.0.1:9", comfyui_url="http://127.0.0.1:9")
    with TestClient(create_app(s)) as c:
        assert c.get("/api/settings").json()["creator"]["channel_name"] == "Tiny Tales"
        body = {"creator": {**CreatorProfile().model_dump(), "website": "https://example.com"}}
        assert c.put("/api/settings", json=body).json()["creator"]["website"] == "https://example.com"
    assert load_profile(tmp_path / "d").website == "https://example.com"
