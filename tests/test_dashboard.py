"""Tests for src/web/dashboard.py – the dashboard page and /api/current."""

import os
import sys
import time as system_time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fastapi.testclient import TestClient
import pytest

from settings import DEFAULT_UI_PASSWORD, DEFAULT_UI_USERNAME, Settings, SettingsStore
from storage import Storage
from web.app import create_app
from mock_ruuvi import MAC_1, MAC_2, make_record


@pytest.mark.skipif(not hasattr(system_time, "tzset"), reason="Requires POSIX local timezone control")
@pytest.mark.parametrize("date", ["2026-03-29", "2026-10-25"])
def test_temperature_summary_boundaries_across_dst(tmp_path, monkeypatch, date):
    import asyncio
    from zoneinfo import ZoneInfo
    import web.dashboard as dashboard

    local_now = datetime.fromisoformat(date + "T09:00:00")

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return local_now if tz is None else local_now.astimezone(tz)

    with monkeypatch.context() as patch:
        patch.setenv("TZ", "Europe/Helsinki")
        system_time.tzset()
        patch.setattr(dashboard, "datetime", FrozenDatetime)
        try:
            storage = Storage(max_entries_per_tag=1, db_path=str(tmp_path / "summary.db"))
            yesterday = (local_now - timedelta(days=1)).date().isoformat()
            for timestamp, temperature in [
                (yesterday + "T21:30:00", -100),
                (yesterday + "T22:30:00", 5),
                (date + "T07:00:00", 20),
                (date + "T08:00:00", 25),
            ]:
                utc_timestamp = datetime.fromisoformat(timestamp).replace(
                    tzinfo=ZoneInfo("Europe/Helsinki"),
                ).astimezone(timezone.utc).isoformat()
                asyncio.run(storage.add(MAC_1, make_record(timestamp=utc_timestamp, temperature=temperature)))

            async def no_history(*args, **kwargs):
                raise AssertionError("Dashboard must not load full history")

            patch.setattr(storage, "get_history", no_history)
            cards = asyncio.run(dashboard._build_dashboard_cards(storage, Settings()))
            assert cards[0]["night_temperature_low"] == 5
            assert cards[0]["night_temperature_high"] == 5
            assert cards[0]["day_temperature_low"] == 20
            assert cards[0]["day_temperature_high"] == 25
            assert cards[0]["temperature_change"] is None
        finally:
            # Restore the process timezone as well as the environment.
            patch.undo()
            system_time.tzset()


@pytest.mark.parametrize("samples,expected", [
    ([(3600, 10), (60, 12.4)], 2.4),
    ([(3300, 14), (3480, 10), (60, 8)], -2.0),
    ([(4200, 10), (60, 12)], 2.0),
    ([(3000, 10), (60, 12)], 2.0),
    ([(4201, 10), (60, 12)], None),
    ([(2999, 10), (60, 12)], None),
    ([(3600, 10), (601, 12)], None),
    ([(3600, 10), (600, 12)], 2.0),
    ([(3600, 10), (60, None)], None),
    ([(3600, None), (60, 12)], None),
    ([(3600, False), (3660, 10), (60, 12)], 2.0),
    ([(3600, "invalid"), (60, 12)], None),
    ([(3900, 10), (3300, 11), (60, 12)], 1.0),
    ([(3600, 10), (3600, 11), (60, 12)], 1.0),
    ([(60, 12)], None),
    ([(3600, 10), (-60, 12)], None),
    ([(3600, 12), (60, 12)], 0.0),
    ([(3600, 12), (60, 11.99)], 0.0),
])
def test_hourly_temperature_change_with_sparse_readings(tmp_path, monkeypatch, samples, expected):
    import asyncio
    import web.dashboard as dashboard

    now = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.astimezone().replace(tzinfo=None)

    client, storage, _ = build_client(tmp_path)
    for age, temperature in samples:
        asyncio.run(storage.add(MAC_1, make_record(
            timestamp=(now - timedelta(seconds=age)).isoformat(), temperature=temperature,
        )))
    monkeypatch.setattr(dashboard, "datetime", FrozenDatetime)
    card = client.get("/api/current").json()["cards"][0]
    assert card["temperature_change"] == expected
    assert "previous_temperature" not in card
    page = client.get("/").text
    assert "Previous temp" not in page
    if expected is None:
        assert 'class="hour-change">—</dd>' in page
    else:
        value = f'{"+" if expected > 0 else ""}{expected:.1f} °C'
        assert f'class="hour-change">{value}</dd>' in page


def build_client(tmp_path):
    storage = Storage(max_entries_per_tag=100, db_path=str(tmp_path / "t.db"))
    settings_store = SettingsStore(settings_path=str(tmp_path / "settings.local.json"))
    app = create_app(storage, settings_store)
    client = TestClient(app)
    client.post(
        "/login",
        data={"username": DEFAULT_UI_USERNAME, "password": DEFAULT_UI_PASSWORD},
    )
    return client, storage, settings_store


def test_dashboard_is_readable_without_login_and_hides_admin_controls(tmp_path):
    storage = Storage(max_entries_per_tag=100, db_path=str(tmp_path / "t.db"))
    settings_store = SettingsStore(settings_path=str(tmp_path / "settings.local.json"))
    app = create_app(storage, settings_store)
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "Dashboard" in response.text
    assert 'href="/login"' in response.text
    assert 'href="/config"' not in response.text
    assert "Log out" not in response.text
    assert "Live logs" not in response.text
    assert "default admin credentials" not in response.text

    client.post(
        "/login",
        data={"username": DEFAULT_UI_USERNAME, "password": DEFAULT_UI_PASSWORD},
    )
    admin_response = client.get("/")
    assert admin_response.status_code == 200
    assert 'href="/config"' in admin_response.text
    assert "Log out" in admin_response.text
    assert "Live logs" in admin_response.text


def test_dashboard_shows_no_tags_message_when_empty(tmp_path):
    client, _, _ = build_client(tmp_path)

    response = client.get("/")

    assert response.status_code == 200
    assert "No tags seen yet" in response.text


def test_dashboard_uses_configured_refresh_interval(tmp_path):
    client, _, settings_store = build_client(tmp_path)
    import asyncio

    asyncio.run(settings_store.update(dashboard_refresh_seconds=600))

    response = client.get("/")

    assert response.status_code == 200
    assert "const DASHBOARD_REFRESH_SECONDS = 600;" in response.text


def test_dashboard_shows_storage_overview_and_api_stats(tmp_path):
    client, storage, _ = build_client(tmp_path)
    import asyncio

    asyncio.run(storage.add(MAC_1, make_record(mac=MAC_1)))

    page = client.get("/")
    api = client.get("/api/current").json()

    assert 'id="storage-overview"' in page.text
    assert "Oldest reading" in page.text
    assert api["storage"]["reading_count"] == 1
    assert api["storage"]["oldest_timestamp"] is not None
    assert api["storage"]["newest_timestamp"] is not None
    assert api["storage"]["database_size_bytes"] > 0
    assert api["storage"]["disk_free_bytes"] > 0


def test_admin_can_hide_storage_overview(tmp_path):
    client, _, settings_store = build_client(tmp_path)
    import asyncio

    asyncio.run(settings_store.update(dashboard_show_storage_info=False))

    page = client.get("/")
    api = client.get("/api/current").json()

    assert 'id="storage-overview"' not in page.text
    assert "storage" not in api


def test_dashboard_shows_friendly_name_and_metrics(tmp_path):
    client, storage, settings_store = build_client(tmp_path)
    import asyncio

    asyncio.run(settings_store.update(tag_names={MAC_1: "Living Room"}))
    asyncio.run(storage.add(MAC_1, make_record(mac=MAC_1, temperature=21.3)))

    response = client.get("/")

    assert response.status_code == 200
    assert "Living Room" in response.text
    assert "21.3" in response.text


def test_dashboard_falls_back_to_mac_when_unnamed(tmp_path):
    client, storage, _ = build_client(tmp_path)
    import asyncio

    asyncio.run(storage.add(MAC_2, make_record(mac=MAC_2)))

    response = client.get("/")

    assert MAC_2 in response.text


def test_api_current_is_readable_without_login(tmp_path):
    storage = Storage(max_entries_per_tag=100, db_path=str(tmp_path / "t.db"))
    settings_store = SettingsStore(settings_path=str(tmp_path / "settings.local.json"))
    app = create_app(storage, settings_store)
    client = TestClient(app)

    response = client.get("/api/current")

    assert response.status_code == 200
    assert response.json()["cards"] == []
    assert response.json()["storage"]["reading_count"] == 0

    client.post(
        "/login",
        data={"username": DEFAULT_UI_USERNAME, "password": DEFAULT_UI_PASSWORD},
    )
    assert client.get("/api/current").status_code == 200


def test_dashboard_shows_name_for_real_world_raw_mac_format(tmp_path):
    """Reproduces GitHub issue #8: a friendly name saved for a tag must
    show up on the dashboard using the exact MAC format real devices
    report -- e.g. a compact lowercase MAC with no separators, as seen
    in the field ("cfb50af67ce4"), not just the colon-separated
    uppercase format used elsewhere in tests.
    """
    client, storage, settings_store = build_client(tmp_path)
    import asyncio

    raw_mac = "cfb50af67ce4"
    asyncio.run(settings_store.update(tag_names={raw_mac: "Kitchen"}))
    asyncio.run(storage.add(raw_mac, make_record(mac=raw_mac)))

    response = client.get("/api/current")

    cards = response.json()["cards"]
    assert len(cards) == 1
    assert cards[0]["name"] == "Kitchen"


def test_dashboard_shows_name_saved_under_legacy_mismatched_mac_key(tmp_path):
    """A pre-existing install may have a ``tag_names`` entry keyed by the
    canonical uppercase colon-separated form (written by the now-removed
    sensor-page rename endpoint before this fix), while storage reports
    readings under the raw, differently-formatted MAC the scanner
    actually uses. The dashboard must still resolve the name instead of
    falling back to showing the raw MAC, without requiring any manual
    settings.local.json edit or migration.
    """
    client, storage, settings_store = build_client(tmp_path)
    import asyncio

    raw_mac = "cfb50af67ce4"
    legacy_canonical_key = "CF:B5:0A:F6:7C:E4"
    asyncio.run(settings_store.update(tag_names={legacy_canonical_key: "Kitchen"}))
    asyncio.run(storage.add(raw_mac, make_record(mac=raw_mac)))

    response = client.get("/api/current")

    cards = response.json()["cards"]
    assert len(cards) == 1
    assert cards[0]["name"] == "Kitchen"


def test_dashboard_preserves_first_name_for_equivalent_mac_keys(tmp_path):
    client, storage, settings_store = build_client(tmp_path)
    import asyncio

    raw_mac = "cfb50af67ce4"
    asyncio.run(settings_store.update(tag_names={
        "CF:B5:0A:F6:7C:E4": "First", raw_mac: "Second",
    }))
    asyncio.run(storage.add(raw_mac, make_record(mac=raw_mac)))
    assert client.get("/api/current").json()["cards"][0]["name"] == "First"


def test_sensor_page_rename_api_no_longer_exists(tmp_path):
    """Only one way to set a tag's friendly name must remain (the Config
    page's bulk settings form); the sensor page's separate inline-rename
    endpoint -- which saved names under a differently-normalized MAC key
    than the dashboard looked them up with -- has been removed.
    """
    client, storage, _ = build_client(tmp_path)
    import asyncio

    asyncio.run(storage.add(MAC_1, make_record(mac=MAC_1)))

    response = client.post(f"/api/sensor/{MAC_1}/name", json={"name": "Kitchen"})

    assert response.status_code == 404


def test_api_current_returns_latest_reading_per_tag(tmp_path):
    client, storage, _ = build_client(tmp_path)
    import asyncio

    timestamp = datetime.now(timezone.utc).isoformat()
    asyncio.run(storage.add(MAC_1, make_record(mac=MAC_1, timestamp=timestamp, temperature=10.0)))
    asyncio.run(storage.add(MAC_1, make_record(mac=MAC_1, timestamp=timestamp, temperature=20.0)))

    response = client.get("/api/current")

    assert response.status_code == 200
    cards = response.json()["cards"]
    assert len(cards) == 1
    assert cards[0]["temperature"] == 20.0


def test_api_current_flags_stale_reading(tmp_path):
    client, storage, settings_store = build_client(tmp_path)
    import asyncio

    asyncio.run(settings_store.update(scan_interval_seconds=1))
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    asyncio.run(storage.add(MAC_1, make_record(mac=MAC_1, timestamp=old_ts)))

    response = client.get("/api/current")

    cards = response.json()["cards"]
    assert cards[0]["stale"] is True


def test_api_current_does_not_flag_recent_reading_as_stale(tmp_path):
    client, storage, settings_store = build_client(tmp_path)
    import asyncio

    asyncio.run(settings_store.update(scan_interval_seconds=60))
    asyncio.run(storage.add(MAC_1, make_record(mac=MAC_1)))

    response = client.get("/api/current")

    cards = response.json()["cards"]
    assert cards[0]["stale"] is False
