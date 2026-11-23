"""Tests for src/history_backfill.py using mocked ruuvitag-sensor."""

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mock_ruuvi import MAC_1, MAC_2  # noqa: E402
from settings import Settings, SettingsStore


def _ts(seconds_offset: int = 0) -> int:
    """A fixed base Unix timestamp plus an offset, for deterministic tests."""
    return 1_700_000_000 + seconds_offset


class TestMergeHistoryEntries:
    def test_merges_split_entries_within_tolerance_window(self):
        from history_backfill import merge_history_entries

        entries = [
            {"temperature": 22.5, "humidity": None, "pressure": None, "timestamp": _ts(0)},
            {"temperature": None, "humidity": 45.0, "pressure": None, "timestamp": _ts(2)},
            {"temperature": None, "humidity": None, "pressure": 1013.2, "timestamp": _ts(1)},
        ]

        merged = merge_history_entries(entries)

        assert len(merged) == 1
        record = merged[0]
        assert record["temperature"] == 22.5
        assert record["humidity"] == 45.0
        assert record["pressure"] == 1013.2
        assert record["timestamp"] == datetime.fromtimestamp(
            _ts(0), tz=timezone.utc
        ).isoformat()

    def test_keeps_distinct_samples_separate(self):
        from history_backfill import merge_history_entries

        entries = [
            {"temperature": 22.5, "humidity": None, "pressure": None, "timestamp": _ts(0)},
            {"temperature": 23.0, "humidity": None, "pressure": None, "timestamp": _ts(600)},
        ]

        merged = merge_history_entries(entries)

        assert len(merged) == 2
        assert merged[0]["temperature"] == 22.5
        assert merged[1]["temperature"] == 23.0

    def test_empty_input_returns_empty_list(self):
        from history_backfill import merge_history_entries

        assert merge_history_entries([]) == []


class TestBackfillTag:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("address, expected", [
        ("aabbccddee01", "AA:BB:CC:DD:EE:01"),
        (MAC_1, MAC_1),
        ("12345678-1234-1234-1234-123456789abc", "12345678-1234-1234-1234-123456789abc"),
    ])
    async def test_connection_address_preserves_stored_sensor_id(self, address, expected):
        from history_backfill import backfill_tag

        download = AsyncMock(return_value=[{"timestamp": _ts(), "temperature": 22.5}])
        with patch("history_backfill.download_history", new=download):
            records = await backfill_tag(address, since=None)

        download.assert_awaited_once_with(expected, start_time=None)
        assert records[0]["mac"] == address

    @pytest.mark.asyncio
    async def test_returns_merged_records_for_fresh_tag(self):
        from history_backfill import backfill_tag

        raw = [
            {"temperature": 22.5, "humidity": None, "pressure": None, "timestamp": _ts(0)},
            {"temperature": None, "humidity": 45.0, "pressure": None, "timestamp": _ts(1)},
        ]
        with patch(
            "history_backfill.download_history",
            new=AsyncMock(return_value=raw),
        ):
            records = await backfill_tag(MAC_1, since=None)

        assert len(records) == 1
        assert records[0]["mac"] == MAC_1
        assert records[0]["temperature"] == 22.5
        assert records[0]["humidity"] == 45.0

    @pytest.mark.asyncio
    async def test_drops_entries_at_or_before_since(self):
        from history_backfill import backfill_tag

        since = datetime.fromtimestamp(_ts(0), tz=timezone.utc).isoformat()
        raw = [
            {"temperature": 22.5, "humidity": None, "pressure": None, "timestamp": _ts(0)},
            {"temperature": 23.0, "humidity": None, "pressure": None, "timestamp": _ts(600)},
        ]
        with patch(
            "history_backfill.download_history",
            new=AsyncMock(return_value=raw),
        ):
            records = await backfill_tag(MAC_1, since=since)

        assert len(records) == 1
        assert records[0]["temperature"] == 23.0

    @pytest.mark.asyncio
    async def test_empty_history_returns_empty_list(self):
        from history_backfill import backfill_tag

        with patch(
            "history_backfill.download_history",
            new=AsyncMock(return_value=[]),
        ):
            records = await backfill_tag(MAC_1, since=None)

        assert records == []

    @pytest.mark.asyncio
    async def test_raises_backfill_error_on_ble_failure(self):
        from history_backfill import backfill_tag, BackfillError

        with patch(
            "history_backfill.download_history",
            new=AsyncMock(side_effect=Exception("BLE error")),
        ):
            with pytest.raises(BackfillError):
                await backfill_tag(MAC_1, since=None)

    @pytest.mark.asyncio
    async def test_uses_configured_timeout_instead_of_hardcoded_default(self):
        """A tag pull that exceeds the *default* timeout but not a longer,
        explicitly-configured one must still succeed -- regression test for
        the fixed 60s _PER_TAG_TIMEOUT_SECONDS that caused issue #9's
        TimeoutErrors on slower devices (e.g. a Raspberry Pi 3)."""
        import asyncio

        from history_backfill import BackfillError, backfill_tag

        raw = [
            {"temperature": 22.5, "humidity": None, "pressure": None, "timestamp": _ts(0)},
        ]

        async def slow_download_history(mac, start_time=None):
            await asyncio.sleep(0.05)
            return raw

        with patch(
            "history_backfill.download_history",
            new=slow_download_history,
        ):
            # A too-short timeout must raise, proving the parameter is honored...
            with pytest.raises(BackfillError):
                await backfill_tag(MAC_1, since=None, timeout_seconds=0.01)

            # ...while a long-enough one succeeds.
            records = await backfill_tag(MAC_1, since=None, timeout_seconds=5)

        assert len(records) == 1
        assert records[0]["mac"] == MAC_1

    @pytest.mark.asyncio
    async def test_raises_backfill_error_on_timeout(self):
        import asyncio

        from history_backfill import backfill_tag, BackfillError

        async def hangs_forever(mac, start_time=None):
            await asyncio.sleep(10)

        with patch(
            "history_backfill.download_history",
            new=hangs_forever,
        ):
            with pytest.raises(BackfillError):
                await backfill_tag(MAC_1, since=None, timeout_seconds=0.01)


class _FakeStorage:
    """Minimal Storage stand-in for run_backfill_for_known_tags tests."""

    def __init__(self, known_macs=None, latest_timestamps=None, backfill_cursors=None):
        self._known_macs = known_macs or []
        self._latest_timestamps = latest_timestamps or {}
        self._backfill_cursors = dict(backfill_cursors or {})
        self.added = []

    async def known_macs(self):
        return list(self._known_macs)

    async def get_latest_timestamp(self, mac):
        return self._latest_timestamps.get(mac)

    async def get_backfill_cursor(self, mac):
        return self._backfill_cursors.get(mac)

    async def set_backfill_cursor(self, mac, timestamp):
        self._backfill_cursors[mac] = timestamp

    async def add_history(self, mac, records):
        self.added.extend((mac, record) for record in records)
        return len(records)


class TestRunBackfillForKnownTags:
    @pytest.mark.asyncio
    async def test_stores_recovered_records(self):
        from history_backfill import run_backfill_for_known_tags

        storage = _FakeStorage(known_macs=[MAC_1])
        settings_store = AsyncMock()
        settings = Settings()

        raw = [{"temperature": 22.5, "humidity": None, "pressure": None, "timestamp": _ts(0)}]
        with patch(
            "history_backfill.download_history",
            new=AsyncMock(return_value=raw),
        ):
            await run_backfill_for_known_tags(settings, storage, settings_store)

        assert len(storage.added) == 1
        assert storage.added[0][0] == MAC_1

    @pytest.mark.asyncio
    async def test_skips_tags_already_disabled(self):
        from history_backfill import run_backfill_for_known_tags

        storage = _FakeStorage(known_macs=[MAC_1])
        settings_store = AsyncMock()
        settings = Settings(history_backfill_disabled_tags={MAC_1: "too many failures"})

        mock_fn = AsyncMock(return_value=[])
        with patch("history_backfill.download_history", new=mock_fn):
            await run_backfill_for_known_tags(settings, storage, settings_store)

        mock_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_auto_disables_after_consecutive_failure_threshold(self, tmp_path):
        from history_backfill import run_backfill_for_known_tags

        storage = _FakeStorage(known_macs=[MAC_1])
        path = str(tmp_path / "settings.json")
        settings_store = SettingsStore(settings_path=path)
        settings = await settings_store.update(history_backfill_failure_threshold=3)

        download = AsyncMock(side_effect=Exception("BLE error"))
        with patch(
            "history_backfill.download_history",
            new=download,
        ):
            await run_backfill_for_known_tags(settings, storage, settings_store)
            for manual in (False, True):
                settings_store = SettingsStore(settings_path=path)
                await run_backfill_for_known_tags(
                    await settings_store.get(), storage, settings_store, recover_recent_history=manual,
                )
            result = await run_backfill_for_known_tags(await settings_store.get(), storage, settings_store)

        final = await settings_store.get()
        assert result["skipped"] == 1
        assert download.await_count == 3
        assert final.history_backfill_disabled_tags == {MAC_1: "3 consecutive backfill failures"}
        assert final.history_backfill_failure_counts == {}

    @pytest.mark.asyncio
    async def test_failure_counter_resets_on_success(self, tmp_path):
        from history_backfill import run_backfill_for_known_tags

        storage = _FakeStorage(known_macs=[MAC_1])
        settings_store = SettingsStore(settings_path=str(tmp_path / "settings.json"))
        settings = await settings_store.update(history_backfill_failure_threshold=2)

        with patch(
            "history_backfill.download_history",
            new=AsyncMock(side_effect=Exception("BLE error")),
        ):
            await run_backfill_for_known_tags(settings, storage, settings_store)
        assert (await settings_store.get()).history_backfill_failure_counts == {MAC_1: 1}

        with patch(
            "history_backfill.download_history",
            new=AsyncMock(return_value=[]),
        ):
            await run_backfill_for_known_tags(settings, storage, settings_store)
        updated = await settings_store.get()
        assert updated.history_backfill_failure_counts == {}
        assert updated.history_backfill_disabled_tags == {}

    @pytest.mark.asyncio
    async def test_whitelist_takes_precedence_over_known_macs(self):
        from history_backfill import run_backfill_for_known_tags

        storage = _FakeStorage(known_macs=[MAC_2])
        settings_store = AsyncMock()
        settings = Settings(tag_addresses=[MAC_1])

        mock_fn = AsyncMock(return_value=[])
        with patch("history_backfill.download_history", new=mock_fn):
            await run_backfill_for_known_tags(settings, storage, settings_store)

        mock_fn.assert_called_once()
        assert mock_fn.call_args.args[0] == MAC_1


@pytest.mark.asyncio
@pytest.mark.parametrize("globally_off", [True, False])
async def test_disabling_failure_limit_resumes_retries_for_affected_tags(tmp_path, globally_off):
    from history_backfill import BackfillError, run_backfill_for_known_tags

    compact_mac = MAC_1.replace(":", "").lower()
    storage = _FakeStorage(known_macs=[compact_mac, MAC_2, "outside-whitelist"])
    path = str(tmp_path / "settings.json")
    store = SettingsStore(settings_path=path)
    await store.update(
        tag_addresses=[compact_mac, MAC_2],
        history_backfill_failure_threshold=2,
        history_backfill_disabled_tags={MAC_1: "2 consecutive backfill failures"},
        history_backfill_failure_counts={MAC_2: 1},
    )
    if globally_off:
        await store.update(history_backfill_failure_limit_enabled=False)
    else:
        await store.update(history_backfill_failure_exempt_tags=[MAC_1])

    store = SettingsStore(settings_path=path)
    download = AsyncMock(side_effect=BackfillError())
    with patch("history_backfill.backfill_tag", new=download):
        automatic = await run_backfill_for_known_tags(await store.get(), storage, store)
        manual = await run_backfill_for_known_tags(
            await store.get(), storage, store, recover_recent_history=True,
        )
    assert automatic["failed"] == 2
    assert manual["failed"] == (2 if globally_off else 1)
    assert manual["skipped"] == (0 if globally_off else 1)
    assert [call.args[0] for call in download.await_args_list] == (
        [compact_mac, MAC_2, compact_mac, MAC_2] if globally_off else [compact_mac, MAC_2, compact_mac]
    )
    final = await store.update(history_backfill_failure_limit_enabled=True, history_backfill_failure_exempt_tags=[])
    assert final.history_backfill_failure_counts == {}
    assert final.history_backfill_disabled_tags == (
        {} if globally_off else {MAC_2: "2 consecutive backfill failures"}
    )


@pytest.mark.asyncio
async def test_failed_persistence_does_not_advance_backfill_cursor(tmp_path):
    import sqlite3
    from storage import Storage
    from settings import SettingsStore
    from history_backfill import run_backfill_for_known_tags

    db = str(tmp_path / "backfill.db")
    storage = Storage(db_path=db)
    store = SettingsStore(settings_path=str(tmp_path / "settings.json"))
    settings = await store.update(tag_addresses=[MAC_1])
    since = "2026-09-24T00:00:00+00:00"
    await storage.set_backfill_cursor(MAC_1, since)
    record = {"mac": MAC_1, "timestamp": "2026-09-25T00:00:00+00:00"}
    second = {**record, "timestamp": "2026-09-25T00:05:00+00:00"}
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TRIGGER fail_insert BEFORE INSERT ON readings
            WHEN NEW.timestamp = '2026-09-25T00:05:00+00:00'
            BEGIN SELECT RAISE(ABORT, 'write failed'); END""")
    with patch("history_backfill.backfill_tag", new=AsyncMock(return_value=[record, second])):
        with pytest.raises(sqlite3.IntegrityError, match="write failed"):
            await run_backfill_for_known_tags(settings, storage, store)
        assert await storage.get_backfill_cursor(MAC_1) == since
        assert await storage.get_history(MAC_1) == []
        assert await storage.get_all() == []
        assert await storage.get_pending() == []
        with sqlite3.connect(db) as conn:
            conn.execute("DROP TRIGGER fail_insert")
        await run_backfill_for_known_tags(settings, storage, store)
    assert await storage.get_history(MAC_1) == [record, second]
    assert await storage.get_backfill_cursor(MAC_1) > since


@pytest.mark.asyncio
@pytest.mark.parametrize(("retention_days", "lookback_days", "cursor_age"), [(0, 10, 0), (3, 3, 4)])
async def test_manual_backfill_recovers_history_without_requeuing_existing_rows(
    tmp_path, retention_days, lookback_days, cursor_age,
):
    from history_backfill import run_backfill_for_known_tags
    from settings import SettingsStore
    from storage import Storage

    now = datetime(2026, 9, 25, 23, 55, tzinfo=timezone.utc)
    cutoff = now - timedelta(days=lookback_days)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    timestamps = [cutoff + timedelta(minutes=5 * i) for i in range(1, 288 * lookback_days + 1)]
    raw = [{"timestamp": int(t.timestamp()), "temperature": 22.0} for t in [cutoff, *timestamps]]
    storage = Storage(db_path=str(tmp_path / "ten-days.db"))
    store = SettingsStore(settings_path=str(tmp_path / "settings.json"))
    settings = await store.update(tag_addresses=[MAC_1], db_retention_days=retention_days)
    existing = {"mac": MAC_1, "timestamp": now.isoformat(), "temperature": 22, "rssi": -65}
    tied = {**existing, "temperature": 23}
    await storage.add(MAC_1, existing)
    await storage.add(MAC_1, tied)
    await storage.clear_pending(await storage.get_pending())
    cursor = (now - timedelta(days=cursor_age, hours=1)).isoformat()
    await storage.set_backfill_cursor(MAC_1, cursor)

    download = AsyncMock(return_value=raw)
    with patch("history_backfill.datetime", FrozenDateTime), patch("history_backfill.download_history", download):
        result = await run_backfill_for_known_tags(settings, storage, store, recover_recent_history=True)
        repeat = await run_backfill_for_known_tags(settings, storage, store, recover_recent_history=True)
    download.assert_awaited_with(MAC_1, start_time=cutoff)
    recovered = len(timestamps) - 1
    assert result == {"succeeded": 1, "failed": 0, "skipped": 0, "recovered": recovered}
    assert repeat["recovered"] == 0
    history = await storage.get_history(MAC_1)
    assert [row["timestamp"] for row in history] == [t.isoformat() for t in timestamps] + [now.isoformat()]
    assert len({row["timestamp"][:10] for row in history}) == lookback_days
    assert history[-2:] == [existing, tied]
    assert len(await storage.get_pending()) == recovered
    assert await storage.get_backfill_cursor(MAC_1) == (now.isoformat() if cursor > cutoff.isoformat() else cursor)


@pytest.mark.asyncio
async def test_pass_reports_success_failure_disabled_and_recovered():
    from history_backfill import run_backfill_for_known_tags, BackfillError

    settings = Settings(history_backfill_disabled_tags={"disabled": "unreachable"})
    storage = _FakeStorage(known_macs=[MAC_1, MAC_2, "disabled"])
    record = {"mac": MAC_1, "timestamp": "2026-09-25T00:00:00+00:00"}
    with patch("history_backfill.backfill_tag", new=AsyncMock(side_effect=[[record], BackfillError()])):
        result = await run_backfill_for_known_tags(settings, storage, AsyncMock(**{"record_backfill_result.return_value": None}))
    assert result == {"succeeded": 1, "failed": 1, "skipped": 1, "recovered": 1}
    assert storage.added == [(MAC_1, record)]
    assert MAC_1 in storage._backfill_cursors
    assert MAC_2 not in storage._backfill_cursors
