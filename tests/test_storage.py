"""Tests for src/storage.py."""

import asyncio
import os
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from storage import Storage
from mock_ruuvi import MAC_1, MAC_2, make_record


@pytest.mark.asyncio
async def test_sqlite_connections_close_after_success_and_failure(tmp_path, monkeypatch):
    import sqlite3
    import storage as storage_module

    opened = []
    original_connect = sqlite3.connect

    def track_connection(*args, **kwargs):
        # Inspect executor-created handles from this test's main thread.
        kwargs["check_same_thread"] = False
        connection = original_connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(storage_module.sqlite3, "connect", track_connection)
    storage = Storage(db_path=str(tmp_path / "connections.db"))
    await storage.add(MAC_1, make_record(mac=MAC_1))
    batch = await storage.get_pending()
    assert len(batch) == 1
    await storage.clear_pending(batch)
    assert await storage.get_pending() == []
    assert len(await storage.get_history(MAC_1)) == 1
    with pytest.raises(KeyError):
        await storage.add(MAC_1, {"mac": MAC_1})
    assert opened
    # Keep references alive: closing must not depend on garbage collection.
    for connection in opened:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


@pytest.mark.asyncio
async def test_temperature_summaries_use_sql_and_persisted_history(tmp_path, monkeypatch):
    import storage as storage_module

    storage = Storage(max_entries_per_tag=1, db_path=str(tmp_path / "summary.db"))
    for hour, temperature in [
        (6, -5), (7, 10), (8, 30), (9, None), (10, "invalid"),
        (11, False), (12, 20), (22, 2), (23, 4),
    ]:
        await storage.add(MAC_1, make_record(
            timestamp=f"2026-10-05T{hour:02}:00:00+00:00", temperature=temperature,
        ))
    # Equal timestamps preserve insertion order for the hourly comparison.
    await storage.add(MAC_1, make_record(timestamp="2026-10-05T23:00:00+00:00", temperature=6))
    # Future samples must not affect the extrema or hourly comparison.
    await storage.add(MAC_1, make_record(timestamp="2026-10-06T06:00:00+00:00", temperature=-100))
    await storage.add(MAC_2, make_record(mac=MAC_2, timestamp="2026-10-05T22:30:00+00:00", temperature=None))
    await storage.add(MAC_2, make_record(mac=MAC_2, timestamp="2026-10-05T23:00:00+00:00", temperature=99))

    def no_decode(*args, **kwargs):
        raise AssertionError("Dashboard summaries must not deserialize reading payloads")

    monkeypatch.setattr(storage_module.json, "loads", no_decode)
    summaries = await storage.get_temperature_summaries(
        [MAC_1, MAC_2, "unknown"], "2026-10-05T07:00:00+00:00", "2026-10-05T22:00:00+00:00",
        "2026-10-05T22:00:00+00:00", "2026-10-06T07:00:00+00:00", "2026-10-06T00:00:00+00:00",
    )
    assert summaries[MAC_1] == {
        "day_temperature_low": 10, "day_temperature_high": 30,
        "night_temperature_low": 2, "night_temperature_high": 6,
        "hour_temperature": 6,
    }
    assert summaries[MAC_2]["night_temperature_low"] == 99
    assert summaries[MAC_2]["hour_temperature"] == 99
    assert all(value is None for value in summaries["unknown"].values())
    assert await storage.get_temperature_summaries([], "", "", "", "", "") == {}


@pytest.mark.asyncio
async def test_storage_stats_get_timestamp_bounds_from_index(tmp_path):
    import sqlite3

    storage = Storage(max_entries_per_tag=10, db_path=str(tmp_path / "stats.db"))
    first = make_record(mac=MAC_1, timestamp="2026-10-05T10:00:00+00:00")
    last = make_record(mac=MAC_1, timestamp="2026-10-05T12:00:00+00:00")
    await storage.add(MAC_1, first)
    await storage.add(MAC_1, last)

    stats = await storage.get_storage_stats()

    assert stats["reading_count"] == 2
    assert stats["oldest_timestamp"] == first["timestamp"]
    assert stats["newest_timestamp"] == last["timestamp"]
    with sqlite3.connect(storage._db_path) as conn:
        count_plan = conn.execute("EXPLAIN SELECT COUNT(*) FROM readings").fetchall()
        assert any(row[1] == "Count" for row in count_plan)
        for query in ("SELECT MIN(timestamp) FROM readings", "SELECT MAX(timestamp) FROM readings"):
            plan = conn.execute(f"EXPLAIN QUERY PLAN {query}").fetchall()
            assert any("idx_readings_timestamp" in row[-1] for row in plan)


def tmp_storage(max_entries=100):
    """Return a Storage instance backed by a temporary SQLite file."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(max_entries_per_tag=max_entries, db_path=tmp.name)


@pytest.mark.asyncio
async def test_add_and_get_by_mac():
    storage = tmp_storage()
    record = make_record(mac=MAC_1)
    await storage.add(MAC_1, record)

    result = await storage.get_by_mac(MAC_1)
    assert len(result) == 1
    assert result[0]["mac"] == MAC_1


@pytest.mark.asyncio
async def test_get_all_returns_all_tags():
    storage = tmp_storage()
    await storage.add(MAC_1, make_record(mac=MAC_1))
    await storage.add(MAC_2, make_record(mac=MAC_2))

    all_records = await storage.get_all()
    assert len(all_records) == 2
    macs = {r["mac"] for r in all_records}
    assert macs == {MAC_1, MAC_2}


@pytest.mark.asyncio
async def test_ring_buffer_drops_oldest():
    storage = tmp_storage(max_entries=3)
    for i in range(5):
        await storage.add(MAC_1, make_record(mac=MAC_1, temperature=float(i)))

    result = await storage.get_by_mac(MAC_1)
    assert len(result) == 3
    # Newest 3 readings (temperatures 2, 3, 4) should remain
    temps = [r["temperature"] for r in result]
    assert temps == [2.0, 3.0, 4.0]


@pytest.mark.asyncio
async def test_unknown_mac_returns_empty():
    storage = tmp_storage()
    result = await storage.get_by_mac("FF:FF:FF:FF:FF:FF")
    assert result == []


@pytest.mark.asyncio
async def test_tag_count():
    storage = tmp_storage()
    assert await storage.tag_count() == 0
    await storage.add(MAC_1, make_record())
    assert await storage.tag_count() == 1
    await storage.add(MAC_2, make_record(mac=MAC_2))
    assert await storage.tag_count() == 2


@pytest.mark.asyncio
async def test_persists_and_reloads(tmp_path):
    """Records written to one Storage instance are loaded by a new one."""
    db = str(tmp_path / "test.db")
    s1 = Storage(max_entries_per_tag=100, db_path=db)
    await s1.add(MAC_1, make_record(mac=MAC_1, temperature=21.0))
    await s1.add(MAC_2, make_record(mac=MAC_2, temperature=22.0))

    # New instance from same db – should reload persisted records
    s2 = Storage(max_entries_per_tag=100, db_path=db)
    all_records = await s2.get_all()
    assert len(all_records) == 2
    macs = {r["mac"] for r in all_records}
    assert macs == {MAC_1, MAC_2}


@pytest.mark.asyncio
async def test_reload_fills_ring_buffer_per_mac(tmp_path):
    """A busy tag must not starve other tags' ring buffers on reload.

    Previously, reload used a single global LIMIT (max_entries * 100)
    across all tags combined, so a very active tag could push a quieter
    tag's readings out of the loaded window. Loading per-MAC fixes this.
    """
    db = str(tmp_path / "test.db")
    s1 = Storage(max_entries_per_tag=3, db_path=db)
    # MAC_1 reports many more times than MAC_2.
    for i in range(10):
        await s1.add(MAC_1, make_record(mac=MAC_1, temperature=float(i)))
    await s1.add(MAC_2, make_record(mac=MAC_2, temperature=99.0))

    s2 = Storage(max_entries_per_tag=3, db_path=db)
    mac1_records = await s2.get_by_mac(MAC_1)
    mac2_records = await s2.get_by_mac(MAC_2)
    assert len(mac1_records) == 3
    assert len(mac2_records) == 1
    assert mac2_records[0]["temperature"] == 99.0


@pytest.mark.asyncio
async def test_pending_queue_drained_after_upload():
    storage = tmp_storage()
    await storage.add(MAC_1, make_record(mac=MAC_1))
    await storage.add(MAC_2, make_record(mac=MAC_2))

    pending = await storage.get_pending()
    assert len(pending) == 2

    await storage.clear_pending(pending)
    assert await storage.get_pending() == []


@pytest.mark.asyncio
async def test_pending_not_duplicated_across_cycles():
    """Simulates two upload cycles: readings must not be re-sent."""
    storage = tmp_storage()
    await storage.add(MAC_1, make_record(mac=MAC_1))

    first_batch = await storage.get_pending()
    await storage.clear_pending(first_batch)

    # No new readings added – second cycle should see nothing pending.
    second_batch = await storage.get_pending()
    assert second_batch == []


@pytest.mark.asyncio
async def test_pending_keeps_new_records_added_during_upload():
    """Records added after a get_pending snapshot survive clear_pending."""
    storage = tmp_storage()
    await storage.add(MAC_1, make_record(mac=MAC_1, temperature=1.0))

    snapshot = await storage.get_pending()
    # Simulate a new reading arriving while the "upload" is in flight.
    await storage.add(MAC_1, make_record(mac=MAC_1, temperature=2.0))
    await storage.clear_pending(snapshot)

    remaining = await storage.get_pending()
    assert len(remaining) == 1
    assert remaining[0]["temperature"] == 2.0


@pytest.mark.asyncio
async def test_prune_removes_old_records(tmp_path):
    import sqlite3
    from datetime import datetime, timedelta, timezone

    db = str(tmp_path / "test.db")
    storage = Storage(max_entries_per_tag=100, db_path=db, retention_days=7)

    old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    recent_ts = datetime.now(timezone.utc).isoformat()
    await storage.add(MAC_1, make_record(mac=MAC_1, timestamp=old_ts))
    await storage.add(MAC_2, make_record(mac=MAC_2, timestamp=recent_ts))

    await storage.prune()

    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT mac FROM readings").fetchall()
    remaining_macs = {row[0] for row in rows}
    assert remaining_macs == {MAC_2}


@pytest.mark.asyncio
async def test_prune_noop_when_retention_disabled(tmp_path):
    import sqlite3
    from datetime import datetime, timedelta, timezone

    db = str(tmp_path / "test.db")
    storage = Storage(max_entries_per_tag=100, db_path=db, retention_days=0)

    old_ts = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    await storage.add(MAC_1, make_record(mac=MAC_1, timestamp=old_ts))

    await storage.prune()

    with sqlite3.connect(db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
    assert count == 1


@pytest.mark.asyncio
async def test_prune_override_takes_precedence_over_constructor_default(tmp_path):
    """A retention_days passed to prune() overrides the constructor value.

    This is how main.py's upload_loop applies a live-edited retention
    setting each cycle without needing to reconstruct Storage.
    """
    import sqlite3
    from datetime import datetime, timedelta, timezone

    db = str(tmp_path / "test.db")
    # Constructed with retention disabled...
    storage = Storage(max_entries_per_tag=100, db_path=db, retention_days=0)

    old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    recent_ts = datetime.now(timezone.utc).isoformat()
    await storage.add(MAC_1, make_record(mac=MAC_1, timestamp=old_ts))
    await storage.add(MAC_2, make_record(mac=MAC_2, timestamp=recent_ts))

    # ...but the caller passes a live override of 7 days, which must win.
    await storage.prune(retention_days=7)

    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT mac FROM readings").fetchall()
    remaining_macs = {row[0] for row in rows}
    assert remaining_macs == {MAC_2}


@pytest.mark.asyncio
async def test_set_max_entries_per_tag_shrinks_and_keeps_newest(tmp_path):
    """Live-resizing the ring buffer keeps the most recent readings per tag."""
    db = str(tmp_path / "test.db")
    storage = Storage(max_entries_per_tag=5, db_path=db)

    for i in range(5):
        await storage.add(MAC_1, make_record(mac=MAC_1, temperature=float(i)))

    await storage.set_max_entries_per_tag(2)

    result = await storage.get_by_mac(MAC_1)
    assert len(result) == 2
    assert [r["temperature"] for r in result] == [3.0, 4.0]

    # New capacity is respected for subsequently added readings too.
    await storage.add(MAC_1, make_record(mac=MAC_1, temperature=5.0))
    result = await storage.get_by_mac(MAC_1)
    assert len(result) == 2
    assert [r["temperature"] for r in result] == [4.0, 5.0]


@pytest.mark.asyncio
async def test_set_max_entries_per_tag_noop_when_unchanged(tmp_path):
    """Calling with the current value is a cheap no-op (safe every loop cycle)."""
    db = str(tmp_path / "test.db")
    storage = Storage(max_entries_per_tag=5, db_path=db)
    await storage.add(MAC_1, make_record(mac=MAC_1))

    await storage.set_max_entries_per_tag(5)

    result = await storage.get_by_mac(MAC_1)
    assert len(result) == 1


@pytest.mark.asyncio
async def test_get_history_returns_all_records_oldest_first(tmp_path):
    db = str(tmp_path / "test.db")
    storage = Storage(max_entries_per_tag=2, db_path=db)  # small ring buffer

    for i in range(5):
        await storage.add(MAC_1, make_record(mac=MAC_1, temperature=float(i)))

    # get_history reads full DB history, independent of the bounded
    # in-memory ring buffer (which only holds the last 2 for this MAC).
    history = await storage.get_history(MAC_1)
    assert [r["temperature"] for r in history] == [0.0, 1.0, 2.0, 3.0, 4.0]


@pytest.mark.asyncio
async def test_get_history_filters_by_since_and_until(tmp_path):
    db = str(tmp_path / "test.db")
    storage = Storage(max_entries_per_tag=100, db_path=db)

    timestamps = [
        "2024-01-01T00:00:00+00:00",
        "2024-01-02T00:00:00+00:00",
        "2024-01-03T00:00:00+00:00",
    ]
    for ts, temp in zip(timestamps, [10.0, 20.0, 30.0]):
        await storage.add(MAC_1, make_record(mac=MAC_1, timestamp=ts, temperature=temp))

    history = await storage.get_history(
        MAC_1, since="2024-01-02T00:00:00+00:00", until="2024-01-02T23:59:59+00:00"
    )
    assert len(history) == 1
    assert history[0]["temperature"] == 20.0


@pytest.mark.asyncio
async def test_get_history_respects_limit_keeping_most_recent(tmp_path):
    db = str(tmp_path / "test.db")
    storage = Storage(max_entries_per_tag=100, db_path=db)

    for i in range(5):
        await storage.add(MAC_1, make_record(mac=MAC_1, temperature=float(i)))

    history = await storage.get_history(MAC_1, limit=2)
    # Most recent 2 readings (temperatures 3, 4), oldest→newest order.
    assert [r["temperature"] for r in history] == [3.0, 4.0]


@pytest.mark.asyncio
async def test_get_history_unknown_mac_returns_empty(tmp_path):
    db = str(tmp_path / "test.db")
    storage = Storage(max_entries_per_tag=100, db_path=db)
    history = await storage.get_history("FF:FF:FF:FF:FF:FF")
    assert history == []


@pytest.mark.asyncio
async def test_known_macs_returns_all_seen_tags(tmp_path):
    db = str(tmp_path / "test.db")
    storage = Storage(max_entries_per_tag=100, db_path=db)
    await storage.add(MAC_1, make_record(mac=MAC_1))
    await storage.add(MAC_2, make_record(mac=MAC_2))

    macs = await storage.known_macs()
    assert set(macs) == {MAC_1, MAC_2}


@pytest.mark.asyncio
async def test_get_history_sampled_returns_all_when_under_cap(tmp_path):
    db = str(tmp_path / "test.db")
    storage = Storage(max_entries_per_tag=100, db_path=db)
    for i in range(10):
        await storage.add(MAC_1, make_record(mac=MAC_1, temperature=float(i)))

    history = await storage.get_history_sampled(MAC_1, max_points=500)
    assert [r["temperature"] for r in history] == [float(i) for i in range(10)]


@pytest.mark.asyncio
async def test_get_history_sampled_caps_and_keeps_endpoints(tmp_path):
    db = str(tmp_path / "test.db")
    storage = Storage(max_entries_per_tag=2000, db_path=db)
    for i in range(1000):
        await storage.add(MAC_1, make_record(mac=MAC_1, temperature=float(i)))

    history = await storage.get_history_sampled(MAC_1, max_points=500)

    assert len(history) <= 500
    assert history[0]["temperature"] == 0.0
    assert history[-1]["temperature"] == 999.0


@pytest.mark.asyncio
async def test_pending_batches_bounded_without_dropping_unsent_records():
    import storage as storage_module

    storage = tmp_storage()
    original_cap = storage_module._MAX_PENDING_RECORDS
    storage_module._MAX_PENDING_RECORDS = 5
    try:
        for i in range(8):
            await storage.add(MAC_1, make_record(mac=MAC_1, temperature=float(i)))
        pending = await storage.get_pending()
        assert len(pending) == 5
        assert [r["temperature"] for r in pending] == [0.0, 1.0, 2.0, 3.0, 4.0]
        await storage.clear_pending(pending)
        assert [r["temperature"] for r in await storage.get_pending()] == [5.0, 6.0, 7.0]
    finally:
        storage_module._MAX_PENDING_RECORDS = original_cap


@pytest.mark.asyncio
async def test_acknowledgement_preserves_unsent_records_after_overflow(tmp_path, monkeypatch):
    monkeypatch.setattr("storage._MAX_PENDING_RECORDS", 3)
    storage = Storage(db_path=str(tmp_path / "queue.db"))
    for value in range(3):
        await storage.add(MAC_1, make_record(temperature=value))
    batch = await storage.get_pending()
    await storage.add(MAC_1, make_record(temperature=99))
    await storage.clear_pending(batch)
    # Retrying the acknowledgement must also leave the new reading alone.
    await storage.clear_pending(batch)
    assert [r["temperature"] for r in await storage.get_pending()] == [99]


@pytest.mark.asyncio
async def test_upload_queue_and_acknowledgements_survive_restart(tmp_path):
    db = str(tmp_path / "durable.db")
    storage = Storage(db_path=db)
    await storage.add(MAC_1, make_record(temperature=10))
    await storage.add(MAC_1, make_record(temperature=20))
    reopened = Storage(db_path=db)
    batch = await reopened.get_pending()
    assert [r["temperature"] for r in batch] == [10, 20]
    await reopened.add(MAC_1, make_record(temperature=30))
    await reopened.clear_pending(batch)
    restarted = Storage(db_path=db)
    assert [r["temperature"] for r in await restarted.get_pending()] == [30]
    assert len(await restarted.get_history(MAC_1)) == 3


@pytest.mark.asyncio
async def test_legacy_database_migrates_pending_readings_once(tmp_path):
    import sqlite3
    import json
    db = str(tmp_path / "legacy.db")
    record = make_record()
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE readings (id INTEGER PRIMARY KEY AUTOINCREMENT, mac TEXT NOT NULL, timestamp TEXT NOT NULL, data TEXT NOT NULL)")
        conn.execute("INSERT INTO readings(mac, timestamp, data) VALUES (?, ?, ?)",
                     (MAC_1, record["timestamp"], json.dumps(record)))
    storage = Storage(db_path=db)
    batch = await storage.get_pending()
    assert batch == [record]
    assert json.loads(json.dumps(batch)) == [record]  # IDs never enter cloud payloads.
    await storage.clear_pending(batch)
    assert await Storage(db_path=db).get_pending() == []


@pytest.mark.asyncio
async def test_history_orders_backfilled_rows_by_measurement_time(tmp_path):
    storage = Storage(db_path=str(tmp_path / "history.db"))
    for day in [5, 1, 4, 2, 3]:
        await storage.add(MAC_1, make_record(
            timestamp=f"2026-09-{day:02d}T00:00:00+00:00", temperature=day))
    assert [r["temperature"] for r in await storage.get_history(MAC_1)] == [1, 2, 3, 4, 5]
    assert [r["temperature"] for r in await storage.get_history(MAC_1, limit=2)] == [4, 5]
    assert [r["temperature"] for r in await storage.get_history_sampled(MAC_1)] == [1, 2, 3, 4, 5]
    sampled = await storage.get_history_sampled(MAC_1, max_points=3)
    assert [r["temperature"] for r in sampled] == [1, 4, 5]
    # Equal measurement times retain deterministic insertion order.
    await storage.add(MAC_1, make_record(timestamp="2026-09-05T00:00:00+00:00", temperature=6))
    assert [r["temperature"] for r in await storage.get_history(MAC_1, limit=2)] == [5, 6]


@pytest.mark.asyncio
async def test_cache_retains_newest_measurements_during_backfill_and_restart(tmp_path):
    db = str(tmp_path / "cache.db")
    storage = Storage(max_entries_per_tag=2, db_path=db)
    for day in [5, 1, 4, 2, 3]:
        await storage.add(MAC_1, make_record(
            timestamp=f"2026-09-{day:02d}T00:00:00+00:00", temperature=day))
    assert [r["temperature"] for r in await storage.get_by_mac(MAC_1)] == [4, 5]
    restarted = Storage(max_entries_per_tag=2, db_path=db)
    assert [r["temperature"] for r in await restarted.get_by_mac(MAC_1)] == [4, 5]
    await restarted.set_max_entries_per_tag(1)
    await restarted.add(MAC_1, make_record(timestamp="2026-09-02T00:00:00+00:00", temperature=2))
    assert [r["temperature"] for r in await restarted.get_by_mac(MAC_1)] == [5]


@pytest.mark.asyncio
async def test_latest_snapshot_survives_backfill_ties_resize_and_restart(tmp_path):
    db = str(tmp_path / "latest.db")
    storage = Storage(max_entries_per_tag=3, db_path=db)
    assert await storage.get_latest_readings() == []
    for day, temperature in [(5, 5), (1, 1), (5, 6), (4, 4)]:
        await storage.add(MAC_1, make_record(
            timestamp=f"2026-09-{day:02d}T00:00:00+00:00", temperature=temperature))
    await storage.add(MAC_2, make_record(mac=MAC_2, temperature=10))
    assert {r["mac"]: r["temperature"] for r in await storage.get_latest_readings()} == {MAC_1: 6, MAC_2: 10}
    await storage.set_max_entries_per_tag(1)
    assert {r["mac"]: r["temperature"] for r in await storage.get_latest_readings()} == {MAC_1: 6, MAC_2: 10}
    restarted = Storage(max_entries_per_tag=1, db_path=db)
    assert await restarted.get_latest_readings() == await storage.get_latest_readings()
    # A snapshot's membership stays stable while later writes are applied.
    snapshot = await restarted.get_latest_readings()
    await asyncio.gather(
        restarted.add(MAC_1, make_record(timestamp="2026-09-06T00:00:00+00:00", temperature=7)),
        restarted.get_latest_readings(),
    )
    assert next(r["temperature"] for r in snapshot if r["mac"] == MAC_1) == 6
    assert next(r["temperature"] for r in await restarted.get_latest_readings() if r["mac"] == MAC_1) == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("max_points", [3, 500])
@pytest.mark.parametrize("count", [0, 1, 499, 500, 501, 1200])
async def test_chart_segments_match_unsampled_reference_with_duplicate_times(tmp_path, max_points, count):
    import json
    import math
    import sqlite3
    from datetime import datetime, timedelta, timezone

    storage = Storage(db_path=str(tmp_path / "segments.db"))
    fields = ("temperature", "humidity", "pressure", "battery")
    timestamp = datetime(2026, 9, 1, tzinfo=timezone.utc)
    records = []
    expected = []
    segments = dict.fromkeys(fields, 0)
    previous = None
    for index in range(count):
        # Duplicate times, live outages, and both sides of the 600-second
        # backfill threshold, including transitions into/out of backfill.
        timestamp += timedelta(seconds=[0, 60, 131, 600, 601][index % 5])
        record = make_record(timestamp=timestamp.isoformat(), temperature=index)
        if index % 7 < 3:
            record.pop("data_format", None)
        else:
            record["data_format"] = 5
        record["battery"] = None
        if index % 11 == 0:
            record.pop("humidity", None)
        if index % 13 == 0:
            record["pressure"] = "invalid"
        if index % 17 == 0:
            record["temperature"] = False
        gap = False
        if previous:
            threshold = 600 if record.get("data_format") is None or previous.get("data_format") is None else 130
            gap = (datetime.fromisoformat(record["timestamp"]) - datetime.fromisoformat(previous["timestamp"])).total_seconds() > threshold
        for field in fields:
            if gap or type(record.get(field)) not in (int, float):
                segments[field] += 1
        records.append(record)
        expected.append({**record, "_chart_segments": segments.copy()})
        previous = record
    with sqlite3.connect(storage._db_path) as conn:
        # Out-of-order insertion, keeping original order for equal times.
        groups = {}
        for record in records:
            groups.setdefault(record["timestamp"], []).append(record)
        for group in reversed(list(groups.values())):
            conn.executemany("INSERT INTO readings(mac, timestamp, data) VALUES (?, ?, ?)",
                             [(MAC_1, r["timestamp"], json.dumps(r)) for r in group])
    step = max(1, math.ceil(len(records) / (max_points - 1))) if count > max_points else 1
    sampled_expected = [r for index, r in enumerate(expected) if index % step == 0 or index == len(records) - 1]
    actual = await storage.get_chart_history(MAC_1, None, timestamp.isoformat(), 130, max_points)
    assert actual == sampled_expected
    assert len(actual) <= max_points


@pytest.mark.asyncio
async def test_chart_count_and_sampling_share_snapshot_during_ingestion(tmp_path, monkeypatch):
    import json
    import sqlite3
    from types import SimpleNamespace

    db = str(tmp_path / "snapshot.db")
    storage = Storage(db_path=db)
    for day in [1, 2, 3]:
        await storage.add(MAC_1, make_record(
            timestamp=f"2026-09-{day:02d}T00:00:00+00:00", temperature=day))
    real_connect = sqlite3.connect
    with real_connect(db) as conn:
        conn.execute("PRAGMA journal_mode=WAL")

    class ConcurrentIngestion(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            cursor = super().execute(sql, parameters)
            if sql.startswith("SELECT COUNT(*) FROM readings"):
                counted = cursor.fetchone()
                cursor.fetchall()
                # A WAL writer commits between the count and sampling.
                record = make_record(timestamp="2026-09-04T00:00:00+00:00", temperature=4)
                with real_connect(db) as writer:
                    writer.execute("INSERT INTO readings(mac, timestamp, data) VALUES (?, ?, ?)",
                                   (MAC_1, record["timestamp"], json.dumps(record)))
                return SimpleNamespace(fetchone=lambda: counted)
            return cursor

    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: real_connect(*args, factory=ConcurrentIngestion, **kwargs))
    sampled = await storage.get_chart_history(MAC_1, None, "2026-09-05T00:00:00+00:00", 130, 3)
    assert [r["temperature"] for r in sampled] == [1, 2, 3]
    with real_connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0] == 4


@pytest.mark.asyncio
async def test_chart_sampling_preserves_metric_gaps_and_outages(tmp_path):
    import json
    import sqlite3
    from datetime import datetime, timedelta, timezone
    db = str(tmp_path / "chart.db")
    storage = Storage(db_path=db)
    base = datetime(2026, 9, 20, tzinfo=timezone.utc)
    records = []
    for index in range(505):
        timestamp = base + timedelta(minutes=index, hours=2 if index >= 251 else 0)
        record = make_record(timestamp=timestamp.isoformat(), temperature=index)
        if index == 1:  # This row will be omitted by sampling.
            record["humidity"] = None
        records.append(record)
    with sqlite3.connect(db) as conn:
        conn.executemany("INSERT INTO readings(mac, timestamp, data) VALUES (?, ?, ?)",
                         [(MAC_1, r["timestamp"], json.dumps(r)) for r in records])
    sampled = await storage.get_chart_history(MAC_1, None, (base + timedelta(days=7)).isoformat(), 130)
    assert len(sampled) <= 500
    by_temperature = {r["temperature"]: r for r in sampled}
    assert 1 not in by_temperature
    assert by_temperature[0]["_chart_segments"]["humidity"] == 0
    assert by_temperature[2]["_chart_segments"]["humidity"] == 1
    assert by_temperature[2]["_chart_segments"]["temperature"] == 0
    assert by_temperature[250]["_chart_segments"]["temperature"] == 0
    assert by_temperature[252]["_chart_segments"]["temperature"] == 1
    assert sampled[0]["timestamp"] == records[0]["timestamp"]
    assert sampled[-1]["timestamp"] == records[-1]["timestamp"]


@pytest.mark.asyncio
async def test_chart_backfill_uses_five_minute_cadence_and_missing_battery(tmp_path):
    storage = Storage(db_path=str(tmp_path / "backfill-chart.db"))
    for minute in [0, 5, 10, 30]:
        await storage.add(MAC_1, {"mac": MAC_1, "timestamp": f"2026-09-20T00:{minute:02d}:00+00:00", "temperature": minute})
    records = await storage.get_chart_history(MAC_1, None, "2026-09-21T00:00:00+00:00", 130)
    assert [r["_chart_segments"]["temperature"] for r in records] == [0, 0, 0, 1]
    assert all("battery" not in r for r in records)
