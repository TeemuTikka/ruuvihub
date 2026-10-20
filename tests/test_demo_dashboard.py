"""Exercise the F5 demo with synthetic data and disposable settings."""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_demo
from web import dashboard


def test_demo_serves_seeded_dashboard_without_touching_regular_files(tmp_path, monkeypatch):
    seed_time = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
    # Garage's latest sample is exactly ten minutes old at seed time.
    now = seed_time + timedelta(seconds=1)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return now.astimezone().replace(tzinfo=None)
            return now.astimezone(tz)

    # Slow CI disk writes must not age the seeded readings before assertions.
    monkeypatch.setattr(dashboard, "datetime", FrozenDatetime)
    regular_settings = tmp_path / "regular-settings.json"
    regular_config = tmp_path / "regular-config.json"
    for path in (regular_settings, regular_config):
        path.write_text("leave this file unchanged", encoding="utf-8")
    monkeypatch.setenv("SETTINGS_PATH", str(regular_settings))
    monkeypatch.setenv("CONFIG_PATH", str(regular_config))
    (tmp_path / "config.json").write_text('{"datetime_format": "iso", "scan_enabled": true}')
    monkeypatch.setattr(run_demo, "ROOT", tmp_path)
    directory = tmp_path / "demo"
    directory.mkdir()
    app = asyncio.run(run_demo.build_demo_app(directory, now=seed_time))
    settings = app.state.settings_store.get_sync()
    assert settings.db_path == str(directory / "demo.db")
    assert settings.scan_enabled is False
    assert settings.cloud_upload_enabled is False
    assert settings.history_backfill_enabled is False
    assert settings.https_enabled is False
    assert settings.dashboard_refresh_seconds == 5
    assert settings.datetime_format == "iso"
    for path in (regular_settings, regular_config):
        assert path.read_text(encoding="utf-8") == "leave this file unchanged"

    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "Demo Living Room" in page.text
        cards = client.get("/api/current").json()["cards"]
        assert len(cards) == 3
        assert sum(card["stale"] for card in cards) == 1
        for card in cards:
            if card["name"] == "Demo Garage":
                assert card["temperature_change"] is None
            else:
                assert card["temperature_change"] is not None
            history = client.get(f'/api/history/{card["mac"]}?range=all').json()
            assert len(history) == 289
            duration = datetime.fromisoformat(history[-1]["timestamp"]) - datetime.fromisoformat(history[0]["timestamp"])
            assert duration.total_seconds() == 86400
            chart = client.get(f'/api/history/{card["mac"]}?range=all&chart=true')
            assert chart.status_code == 200
            assert chart.json()["records"]


def test_demo_server_binds_locally_and_cleans_up(monkeypatch):
    import uvicorn

    served_directories = []

    class DemoServer:
        def __init__(self, config):
            assert config.host == "127.0.0.1"
            assert config.port == 8081
            self.app = config.app

        async def serve(self):
            path = Path(self.app.state.settings_store.get_sync().db_path)
            assert path.exists()
            served_directories.append(path.parent)

    monkeypatch.setattr(uvicorn, "Server", DemoServer)
    asyncio.run(run_demo.main(port=8081))
    assert len(served_directories) == 1
    assert not served_directories[0].exists()
