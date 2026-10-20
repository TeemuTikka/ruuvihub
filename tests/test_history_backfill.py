"""Tests for src/history_backfill.py using mocked ruuvitag-sensor."""

import os
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mock_ruuvi import MAC_1, MAC_2  # noqa: E402


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
    async def test_returns_merged_records_for_fresh_tag(self):
        from history_backfill import backfill_tag

        raw = [
            {"temperature": 22.5, "humidity": None, "pressure": None, "timestamp": _ts(0)},
            {"temperature": None, "humidity": 45.0, "pressure": None, "timestamp": _ts(1)},
        ]
        with patch(
            "history_backfill.RuuviTagSensor.download_history",
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
            "history_backfill.RuuviTagSensor.download_history",
            new=AsyncMock(return_value=raw),
        ):
            records = await backfill_tag(MAC_1, since=since)

        assert len(records) == 1
        assert records[0]["temperature"] == 23.0

    @pytest.mark.asyncio
    async def test_empty_history_returns_empty_list(self):
        from history_backfill import backfill_tag

        with patch(
            "history_backfill.RuuviTagSensor.download_history",
            new=AsyncMock(return_value=[]),
        ):
            records = await backfill_tag(MAC_1, since=None)

        assert records == []

    @pytest.mark.asyncio
    async def test_raises_backfill_error_on_ble_failure(self):
        from history_backfill import backfill_tag, BackfillError

        with patch(
            "history_backfill.RuuviTagSensor.download_history",
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
            "history_backfill.RuuviTagSensor.download_history",
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
            "history_backfill.RuuviTagSensor.download_history",
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

    async def add(self, mac, record):
        self.added.append((mac, record))


class _FakeSettingsStore:
    """Minimal SettingsStore stand-in tracking set_backfill_disabled calls."""

    def __init__(self):
        self.disabled_calls = []
        self.failure_count_calls = []

    async def set_backfill_disabled(self, mac, reason):
        self.disabled_calls.append((mac, reason))

    async def set_backfill_failure_count(self, mac, count):
        self.failure_count_calls.append((mac, count))


class _FakeSettings:
    def __init__(
        self,
        tag_addresses=None,
        disabled_tags=None,
        failure_threshold=5,
        failure_counts=None,
        timeout_seconds=120,
    ):
        self.tag_addresses = tag_addresses or []
        self.history_backfill_disabled_tags = disabled_tags or {}
        self.history_backfill_failure_threshold = failure_threshold
        self.history_backfill_failure_counts = failure_counts or {}
        self.history_backfill_timeout_seconds = timeout_seconds


class TestRunBackfillForKnownTags:
    @pytest.mark.asyncio
    async def test_stores_recovered_records(self):
        from history_backfill import run_backfill_for_known_tags, _consecutive_failures

        _consecutive_failures.clear()
        storage = _FakeStorage(known_macs=[MAC_1])
        settings_store = _FakeSettingsStore()
        settings = _FakeSettings()

        raw = [{"temperature": 22.5, "humidity": None, "pressure": None, "timestamp": _ts(0)}]
        with patch(
            "history_backfill.RuuviTagSensor.download_history",
            new=AsyncMock(return_value=raw),
        ):
            await run_backfill_for_known_tags(settings, storage, settings_store)

        assert len(storage.added) == 1
        assert storage.added[0][0] == MAC_1

    @pytest.mark.asyncio
    async def test_skips_tags_already_disabled(self):
        from history_backfill import run_backfill_for_known_tags, _consecutive_failures

        _consecutive_failures.clear()
        storage = _FakeStorage(known_macs=[MAC_1])
        settings_store = _FakeSettingsStore()
        settings = _FakeSettings(disabled_tags={MAC_1: "too many failures"})

        mock_fn = AsyncMock(return_value=[])
        with patch("history_backfill.RuuviTagSensor.download_history", new=mock_fn):
            await run_backfill_for_known_tags(settings, storage, settings_store)

        mock_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_auto_disables_after_consecutive_failure_threshold(self):
        from history_backfill import run_backfill_for_known_tags, _consecutive_failures

        _consecutive_failures.clear()
        storage = _FakeStorage(known_macs=[MAC_1])
        settings_store = _FakeSettingsStore()
        settings = _FakeSettings(failure_threshold=3)

        with patch(
            "history_backfill.RuuviTagSensor.download_history",
            new=AsyncMock(side_effect=Exception("BLE error")),
        ):
            for _ in range(3):
                await run_backfill_for_known_tags(settings, storage, settings_store)

        assert len(settings_store.disabled_calls) == 1
        assert settings_store.disabled_calls[0][0] == MAC_1

    @pytest.mark.asyncio
    async def test_failure_counter_resets_on_success(self):
        from history_backfill import run_backfill_for_known_tags, _consecutive_failures

        _consecutive_failures.clear()
        storage = _FakeStorage(known_macs=[MAC_1])
        settings_store = _FakeSettingsStore()
        settings = _FakeSettings(failure_threshold=2)

        with patch(
            "history_backfill.RuuviTagSensor.download_history",
            new=AsyncMock(side_effect=Exception("BLE error")),
        ):
            await run_backfill_for_known_tags(settings, storage, settings_store)
        assert _consecutive_failures[MAC_1] == 1

        with patch(
            "history_backfill.RuuviTagSensor.download_history",
            new=AsyncMock(return_value=[]),
        ):
            await run_backfill_for_known_tags(settings, storage, settings_store)
        assert MAC_1 not in _consecutive_failures
        assert settings_store.disabled_calls == []

    @pytest.mark.asyncio
    async def test_whitelist_takes_precedence_over_known_macs(self):
        from history_backfill import run_backfill_for_known_tags, _consecutive_failures

        _consecutive_failures.clear()
        storage = _FakeStorage(known_macs=[MAC_2])
        settings_store = _FakeSettingsStore()
        settings = _FakeSettings(tag_addresses=[MAC_1])

        mock_fn = AsyncMock(return_value=[])
        with patch("history_backfill.RuuviTagSensor.download_history", new=mock_fn):
            await run_backfill_for_known_tags(settings, storage, settings_store)

        mock_fn.assert_called_once()
        assert mock_fn.call_args.args[0] == MAC_1


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
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TRIGGER fail_insert BEFORE INSERT ON readings BEGIN SELECT RAISE(ABORT, 'write failed'); END")
    with patch("history_backfill.backfill_tag", new=AsyncMock(return_value=[record])):
        with pytest.raises(sqlite3.IntegrityError, match="write failed"):
            await run_backfill_for_known_tags(settings, storage, store)
        assert await storage.get_backfill_cursor(MAC_1) == since
        assert await storage.get_history(MAC_1) == []
        assert await storage.get_all() == []
        assert await storage.get_pending() == []
        with sqlite3.connect(db) as conn:
            conn.execute("DROP TRIGGER fail_insert")
        await run_backfill_for_known_tags(settings, storage, store)
    assert await storage.get_history(MAC_1) == [record]
    assert await storage.get_backfill_cursor(MAC_1) > since
