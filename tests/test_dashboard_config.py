"""Dashboard preferences require admin access and save independently."""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dashboard_preferences import DEFAULT_DASHBOARD_METRICS
from mock_ruuvi import MAC_1, MAC_2, make_record
from settings import DEFAULT_UI_USERNAME, DEFAULT_UI_PASSWORD, Settings, SettingsStore
from storage import Storage
from web.app import create_app
from web.auth import compute_csrf_token


def setup(tmp_path):
    store = SettingsStore(settings_path=str(tmp_path / "settings.local.json"))
    storage = Storage(db_path=str(tmp_path / "synthetic.db"))
    asyncio.run(store.update(tag_addresses=[MAC_1, MAC_2], tag_names={MAC_1: "Living room"}))
    return TestClient(create_app(storage, store)), store, storage


def sign_in(client, store):
    client.post("/login", data={"username": DEFAULT_UI_USERNAME, "password": DEFAULT_UI_PASSWORD})
    settings = store.get_sync()
    return compute_csrf_token(settings.session_secret, settings.ui_username)


def form(token, **overrides):
    data = {
        "csrf_token": token, "dashboard_refresh_seconds": "30", "dashboard_change_hours": "1",
        "visible_metrics": DEFAULT_DASHBOARD_METRICS, "known_tags": [MAC_1, MAC_2],
        "visible_tags": [MAC_1, MAC_2], "dashboard_show_storage_info": "on",
    }
    data.update(overrides)
    return data


def test_dashboard_preferences_require_login_and_csrf(tmp_path):
    client, store, _ = setup(tmp_path)
    for method in ("get", "post"):
        response = getattr(client, method)("/config/dashboard", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"
    sign_in(client, store)
    response = client.post("/config/dashboard", data=form(""))
    assert response.status_code == 403
    assert store.get_sync().dashboard_change_hours == 1


def test_dashboard_preferences_persist_without_changing_system_settings(tmp_path):
    client, store, storage = setup(tmp_path)
    token = sign_in(client, store)
    asyncio.run(store.update(scan_interval_seconds=73))
    response = client.post("/config/dashboard", data=form(
        token, visible_metrics=["temperature", "humidity"], visible_tags=[MAC_1],
        dashboard_change_hours="3", dashboard_show_storage_info="on",
    ), follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/config/dashboard?saved=1"
    saved = store.get_sync()
    assert saved.dashboard_change_hours == 3
    assert saved.dashboard_visible_metrics == ["temperature", "humidity"]
    assert saved.dashboard_hidden_tags == [MAC_2]
    assert saved.dashboard_refresh_seconds == 30
    assert saved.scan_interval_seconds == 73
    assert saved.tag_names == {MAC_1: "Living room"}
    reloaded = SettingsStore(settings_path=str(tmp_path / "settings.local.json")).get_sync()
    assert reloaded.dashboard_change_hours == 3
    assert reloaded.dashboard_hidden_tags == [MAC_2]
    for mac in (MAC_1, MAC_2):
        asyncio.run(storage.add(mac, make_record(mac=mac)))
    page = client.get("/").text
    assert f'data-mac="{MAC_1}"' in page and f'data-mac="{MAC_2}"' not in page
    assert 'data-metric="pressure" hidden' in page
    assert 'data-metric="temperature" hidden' not in page
    assert "Change / 3h" in page
    api = client.get("/api/current").json()
    assert [card["mac"] for card in api["cards"]] == [MAC_1]
    assert api["dashboard_options"]["change_hours"] == 3
    # Hiding a sensor on the dashboard keeps its history available.
    assert client.get(f"/api/history/{MAC_2}?range=all").json()
    admin_page = client.get("/config/dashboard").text
    assert "Dashboard settings saved" in client.get("/config/dashboard?saved=1").text
    assert 'name="dashboard_change_hours"' in admin_page
    assert 'name="dashboard_change_hours"' not in client.get("/config").text


@pytest.mark.parametrize("overrides", [
    {"dashboard_change_hours": "0"}, {"dashboard_change_hours": "-1"},
    {"dashboard_change_hours": "1.5"}, {"dashboard_change_hours": "169"},
    {"dashboard_change_hours": "invalid"}, {"dashboard_refresh_seconds": "0"},
    {"visible_metrics": ["unknown"]}, {"visible_tags": ["unknown"]},
    {"known_tags": ["unknown"]},
])
def test_invalid_dashboard_preferences_do_not_change_any_settings(tmp_path, overrides):
    client, store, _ = setup(tmp_path)
    token = sign_in(client, store)
    before = store.get_sync().to_dict()
    response = client.post("/config/dashboard", data=form(token, **overrides), follow_redirects=False)
    assert response.headers["location"] == "/config/dashboard?error=invalid_values"
    assert store.get_sync().to_dict() == before


def test_empty_selections_hide_all_then_can_be_restored(tmp_path):
    client, store, storage = setup(tmp_path)
    token = sign_in(client, store)
    for mac in (MAC_1, MAC_2):
        asyncio.run(storage.add(mac, make_record(mac=mac)))
    data = form(token, visible_metrics=[], visible_tags=[])
    data.pop("dashboard_show_storage_info")
    client.post("/config/dashboard", data=data)
    assert store.get_sync().dashboard_visible_metrics == []
    assert client.get("/api/current").json()["cards"] == []
    assert "storage" not in client.get("/api/current").json()
    # A new sensor discovered after the form was loaded is visible by default.
    new_mac = "AA:BB:CC:DD:EE:03"
    asyncio.run(storage.add(new_mac, make_record(mac=new_mac)))
    client.post("/config/dashboard", data=data)
    assert [card["mac"] for card in client.get("/api/current").json()["cards"]] == [new_mac]
    client.post("/config/dashboard", data=form(token))
    assert len(client.get("/api/current").json()["cards"]) == 3


@pytest.mark.parametrize("hours", [1, 3, 24, 168])
def test_comparison_uses_selected_period(tmp_path, monkeypatch, hours):
    import web.dashboard as dashboard
    client, store, storage = setup(tmp_path)
    now = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.astimezone().replace(tzinfo=None)

    asyncio.run(store.update(dashboard_change_hours=hours))
    for timestamp, temperature in [(now - timedelta(hours=hours, minutes=3), 10), (now - timedelta(seconds=10), 12)]:
        asyncio.run(storage.add(MAC_1, make_record(timestamp=timestamp.isoformat(), temperature=temperature)))
    monkeypatch.setattr(dashboard, "datetime", FrozenDatetime)
    assert client.get("/api/current").json()["cards"][0]["temperature_change"] == 2


def test_preferences_validate_legacy_files_and_normalize_hidden_mac(tmp_path):
    assert Settings.from_dict({}).dashboard_change_hours == 1
    assert Settings.from_dict({}).dashboard_visible_metrics == DEFAULT_DASHBOARD_METRICS
    for value in [0, -1, True, "3", 169, None]:
        assert Settings.from_dict({"dashboard_change_hours": value}).dashboard_change_hours == 1
    assert Settings.from_dict({"dashboard_visible_metrics": ["unknown"]}).dashboard_visible_metrics == DEFAULT_DASHBOARD_METRICS
    assert Settings.from_dict({"dashboard_visible_metrics": []}).dashboard_visible_metrics == []
    client, store, storage = setup(tmp_path)
    asyncio.run(storage.add(MAC_1, make_record()))
    asyncio.run(store.update(dashboard_hidden_tags=[MAC_1.replace(":", "").lower()]))
    assert client.get("/api/current").json()["cards"] == []
