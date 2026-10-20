"""Tests for the per-sensor history page, history API, and CSV export."""

import asyncio
import csv
import io
import os
import sys
from unittest.mock import patch

import pytest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fastapi.testclient import TestClient

from settings import DEFAULT_UI_PASSWORD, DEFAULT_UI_USERNAME, SettingsStore
from storage import Storage
from web.app import create_app

from mock_ruuvi import MAC_1, MAC_2, make_record


def build_app(tmp_path):
    storage = Storage(max_entries_per_tag=100, db_path=str(tmp_path / "t.db"))
    settings_store = SettingsStore(settings_path=str(tmp_path / "settings.local.json"))
    app = create_app(storage, settings_store)
    return app, storage, settings_store


def login(client: TestClient):
    response = client.post(
        "/login",
        data={"username": DEFAULT_UI_USERNAME, "password": DEFAULT_UI_PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_sensor_page_is_readable_without_login_and_hides_admin_controls(tmp_path):
    app, _, _ = build_app(tmp_path)
    client = TestClient(app)

    response = client.get(f"/sensor/{MAC_1}")

    assert response.status_code == 200
    assert 'id="sensor-page"' in response.text
    assert 'href="/login"' in response.text
    assert 'href="/config"' not in response.text
    assert "Rename this tag" not in response.text
    assert "Log out" not in response.text
    assert "Live logs" not in response.text

    login(client)
    admin_response = client.get(f"/sensor/{MAC_1}")
    assert admin_response.status_code == 200
    assert 'href="/config"' in admin_response.text
    assert "Rename this tag" in admin_response.text
    assert "Log out" in admin_response.text
    assert "Live logs" in admin_response.text


def test_sensor_page_uses_friendly_name_when_configured(tmp_path):
    app, _, settings_store = build_app(tmp_path)
    client = TestClient(app)
    asyncio.run(settings_store.update(tag_names={MAC_1: "Living Room"}))
    login(client)

    response = client.get(f"/sensor/{MAC_1}")

    assert response.status_code == 200
    assert "Living Room" in response.text
    assert f'data-mac="{MAC_1}"' in response.text
    assert 'data-range="24h"' in response.text


def test_sensor_page_falls_back_to_mac_when_unnamed(tmp_path):
    app, _, _ = build_app(tmp_path)
    client = TestClient(app)
    login(client)

    response = client.get(f"/sensor/{MAC_2}")

    assert response.status_code == 200
    assert MAC_2 in response.text


def test_history_api_is_readable_without_login(tmp_path):
    app, _, _ = build_app(tmp_path)
    client = TestClient(app)

    response = client.get(f"/api/history/{MAC_1}")

    assert response.status_code == 200
    assert response.json() == []

    login(client)
    assert client.get(f"/api/history/{MAC_1}").status_code == 200


def test_history_api_filters_by_range(tmp_path):
    app, storage, _ = build_app(tmp_path)
    client = TestClient(app)
    login(client)
    now = datetime.now(timezone.utc)
    old_record = make_record(mac=MAC_1, timestamp=(now - timedelta(days=40)).isoformat())
    medium_record = make_record(
        mac=MAC_1,
        timestamp=(now - timedelta(days=2)).isoformat(),
        temperature=19.2,
    )
    recent_record = make_record(
        mac=MAC_1,
        timestamp=(now - timedelta(minutes=30)).isoformat(),
        temperature=21.7,
    )
    for record in (old_record, medium_record, recent_record):
        asyncio.run(storage.add(MAC_1, record))

    day_response = client.get(f"/api/history/{MAC_1}?range=24h")
    week_response = client.get(f"/api/history/{MAC_1}?range=7d")
    all_response = client.get(f"/api/history/{MAC_1}?range=all")

    assert day_response.status_code == 200
    assert [row["timestamp"] for row in day_response.json()] == [recent_record["timestamp"]]
    assert [row["timestamp"] for row in week_response.json()] == [
        medium_record["timestamp"],
        recent_record["timestamp"],
    ]
    assert [row["timestamp"] for row in all_response.json()] == [
        old_record["timestamp"],
        medium_record["timestamp"],
        recent_record["timestamp"],
    ]


def test_history_api_returns_empty_list_for_unknown_mac(tmp_path):
    app, _, _ = build_app(tmp_path)
    client = TestClient(app)
    login(client)

    response = client.get("/api/history/AA:BB:CC:DD:EE:99")

    assert response.status_code == 200
    assert response.json() == []


def test_history_api_accepts_compact_lowercase_mac(tmp_path):
    app, storage, _ = build_app(tmp_path)
    client = TestClient(app)
    login(client)
    record = make_record(mac=MAC_1)
    asyncio.run(storage.add(MAC_1, record))

    compact_lower = MAC_1.replace(":", "").lower()
    response = client.get(f"/api/history/{compact_lower}?range=all")

    assert response.status_code == 200
    assert [row["timestamp"] for row in response.json()] == [record["timestamp"]]


def test_sensor_page_accepts_compact_lowercase_mac(tmp_path):
    app, storage, settings_store = build_app(tmp_path)
    client = TestClient(app)
    asyncio.run(settings_store.update(tag_names={MAC_1: "Living Room"}))
    record = make_record(mac=MAC_1)
    asyncio.run(storage.add(MAC_1, record))
    login(client)

    compact_lower = MAC_1.replace(":", "").lower()
    response = client.get(f"/sensor/{compact_lower}")

    assert response.status_code == 200
    assert "Living Room" in response.text
    assert f'data-mac="{MAC_1}"' in response.text


def test_sensor_page_and_history_resolve_when_real_mac_is_raw_compact_form(tmp_path):
    """When a tag's *real* MAC (as reported by the scanner and used as
    the storage/tag_names key) is a raw compact lowercase hex string --
    e.g. "cfb50af67ce4", as seen on real hardware -- visiting the sensor
    page with a canonicalized uppercase colon-separated URL (the more
    "obvious"/typed-by-hand format) must still find both the friendly
    name and the tag's history. Blindly canonicalizing the URL's MAC
    (the previous behavior) does not match storage's raw key and
    silently returns no history and no name.
    """
    app, storage, settings_store = build_app(tmp_path)
    client = TestClient(app)
    raw_mac = "cfb50af67ce4"
    asyncio.run(settings_store.update(tag_names={raw_mac: "Kitchen"}))
    record = make_record(mac=raw_mac)
    asyncio.run(storage.add(raw_mac, record))
    login(client)

    canonical_form = "CF:B5:0A:F6:7C:E4"
    page_response = client.get(f"/sensor/{canonical_form}")
    history_response = client.get(f"/api/history/{canonical_form}?range=all")

    assert page_response.status_code == 200
    assert "Kitchen" in page_response.text
    assert f'data-mac="{raw_mac}"' in page_response.text
    assert history_response.status_code == 200
    assert [row["timestamp"] for row in history_response.json()] == [record["timestamp"]]


def test_sensor_page_and_history_resolve_when_storage_and_settings_keys_mismatch(tmp_path):
    """Regression test for a legacy state where storage and tag_names key
    the *same* physical tag under two different textual shapes: storage
    has the scanner's raw compact form, while tag_names was saved earlier
    under the canonical uppercase colon-separated form. Merging both key
    sets into one lookup (the previous, buggy behavior) makes the
    resolved MAC depend on set/dict iteration order -- sometimes
    returning the tag_names key (so the storage-backed history query
    then finds nothing) and sometimes the storage key (so an exact
    tag_names.get() then misses the name). Both the friendly name and
    the history must be found regardless.
    """
    app, storage, settings_store = build_app(tmp_path)
    client = TestClient(app)
    raw_mac = "cfb50af67ce4"
    canonical_mac = "CF:B5:0A:F6:7C:E4"
    asyncio.run(settings_store.update(tag_names={canonical_mac: "Kitchen"}))
    record = make_record(mac=raw_mac)
    asyncio.run(storage.add(raw_mac, record))
    login(client)

    page_response = client.get(f"/sensor/{canonical_mac}")
    history_response = client.get(f"/api/history/{canonical_mac}?range=all")

    assert page_response.status_code == 200
    assert "Kitchen" in page_response.text
    assert f'data-mac="{raw_mac}"' in page_response.text
    assert history_response.status_code == 200
    assert [row["timestamp"] for row in history_response.json()] == [record["timestamp"]]


def test_history_export_returns_csv(tmp_path):
    app, storage, _ = build_app(tmp_path)
    client = TestClient(app)
    assert "ruuvihub_session" not in client.cookies
    now = datetime.now(timezone.utc)
    first_record = make_record(
        mac=MAC_1,
        timestamp=(now - timedelta(hours=1)).isoformat(),
        temperature=20.1,
        humidity=41.5,
        pressure=1011.2,
        battery=2945,
    )
    second_record = make_record(
        mac=MAC_1,
        timestamp=now.isoformat(),
        temperature=21.4,
        humidity=43.0,
        pressure=1012.8,
        battery=2940,
    )
    for record in (first_record, second_record):
        asyncio.run(storage.add(MAC_1, record))

    response = client.get(f"/api/history/{MAC_1}/export?range=24h")

    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert 'attachment; filename="AA-BB-CC-DD-EE-01_24h.csv"' == response.headers[
        "content-disposition"
    ]

    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert rows == [
        {
            "timestamp": first_record["timestamp"],
            "temperature": "20.1",
            "humidity": "41.5",
            "pressure": "1011.2",
            "battery": "2945",
        },
        {
            "timestamp": second_record["timestamp"],
            "temperature": "21.4",
            "humidity": "43.0",
            "pressure": "1012.8",
            "battery": "2940",
        },
    ]


def test_history_api_downsamples_long_ranges(tmp_path):
    app, storage, _ = build_app(tmp_path)
    client = TestClient(app)
    login(client)
    base = datetime.now(timezone.utc) - timedelta(days=6)

    for index in range(505):
        record = make_record(
            mac=MAC_1,
            timestamp=(base + timedelta(minutes=index)).isoformat(),
            temperature=20.0 + (index / 1000),
        )
        asyncio.run(storage.add(MAC_1, record))

    response = client.get(f"/api/history/{MAC_1}?range=7d")
    payload = response.json()

    assert response.status_code == 200
    assert len(payload) <= 500
    assert payload[0]["timestamp"] == (base + timedelta(minutes=0)).isoformat()
    assert payload[-1]["timestamp"] == (base + timedelta(minutes=504)).isoformat()


@pytest.mark.parametrize("range_value,hours", [("1h", 1), ("24h", 24), ("7d", 168), ("30d", 720)])
def test_chart_uses_exact_selected_bounds_even_with_sparse_or_empty_data(tmp_path, range_value, hours):
    app, storage, _ = build_app(tmp_path)
    client = TestClient(app)
    login(client)
    now = datetime(2026, 9, 26, 12, tzinfo=timezone.utc)
    start = now - timedelta(hours=hours)
    records = [make_record(timestamp=ts.isoformat()) for ts in
               (start - timedelta(seconds=1), start, now - timedelta(minutes=5), now + timedelta(seconds=1))]
    for record in records:
        asyncio.run(storage.add(MAC_1, record))
    with patch("web.sensor.datetime") as clock:
        clock.now.return_value = now
        payload = client.get(f"/api/history/{MAC_1}?range={range_value}&chart=true").json()
        empty = client.get(f"/api/history/{MAC_2}?range={range_value}&chart=true").json()
        raw = client.get(f"/api/history/{MAC_1}?range={range_value}").json()
    assert payload["since"] == start.isoformat()
    assert payload["until"] == now.isoformat()
    assert [row["timestamp"] for row in payload["records"]] == [records[1]["timestamp"], records[2]["timestamp"]]
    assert [row["timestamp"] for row in raw] == [records[1]["timestamp"], records[2]["timestamp"]]
    assert empty == {"since": start.isoformat(), "until": now.isoformat(), "records": []}


def test_chart_all_range_starts_at_oldest_measurement(tmp_path):
    app, storage, _ = build_app(tmp_path)
    client = TestClient(app)
    login(client)
    oldest = make_record(timestamp=(datetime.now(timezone.utc) - timedelta(days=45)).isoformat())
    asyncio.run(storage.add(MAC_1, oldest))
    payload = client.get(f"/api/history/{MAC_1}?range=all&chart=true").json()
    assert payload["since"] == oldest["timestamp"]
    assert payload["records"][0]["temperature"] == oldest["temperature"]
