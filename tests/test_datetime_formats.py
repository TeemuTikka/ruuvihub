"""Datetime display configuration persists and renders consistently."""

import asyncio
from datetime import datetime
import json
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from datetime_formats import format_timestamp
from settings import Settings, SettingsStore
from storage import Storage
from web.app import create_app


@pytest.mark.parametrize("display_format,pattern", [
    ("fi", "%d.%m.%Y %H:%M:%S"), ("iso", "%Y-%m-%d %H:%M:%S"),
])
def test_datetime_format_bootstrap_persistence_and_html(tmp_path, display_format, pattern):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"datetime_format": display_format}))
    settings_path = str(tmp_path / "settings.local.json")
    store = SettingsStore(settings_path=settings_path, config_path=str(config))
    assert store.get_sync().datetime_format == display_format
    assert SettingsStore(settings_path=settings_path).get_sync().datetime_format == display_format
    storage = Storage(db_path=str(tmp_path / "synthetic.db"))
    timestamp = "2026-10-06T18:05:09+00:00"
    asyncio.run(storage.add("demo", {"mac": "demo", "timestamp": timestamp, "temperature": 20}))
    with TestClient(create_app(storage, store)) as client:
        page = client.get("/")
        assert page.status_code == 200
        expected = datetime.fromisoformat(timestamp).astimezone().strftime(pattern)
        assert f"Last seen: {expected}" in page.text
        for field in ("oldest", "newest"):
            assert f'<time id="storage-{field}" datetime="{timestamp}">{expected}</time>' in page.text
        footer = page.text.split('<footer class="app-footer">', 1)[1].split('</footer>', 1)[0]
        assert 'id="storage-overview"' in footer
        assert "<h2" not in footer
        assert f'window.RUUVIHUB_DATETIME_FORMAT = "{display_format}"' in page.text
        assert client.get("/api/current").json()["cards"][0]["timestamp"] == timestamp


def test_legacy_settings_and_invalid_formats_default_to_finnish():
    assert Settings.from_dict({}).datetime_format == "fi"
    for value in [None, False, [], {}, "", "invalid"]:
        assert Settings.from_dict({"datetime_format": value}).datetime_format == "fi"
    assert format_timestamp(None) == "—"
    assert format_timestamp("invalid") == "invalid"
