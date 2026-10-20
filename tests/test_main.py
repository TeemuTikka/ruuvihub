"""Tests for src/main.py loop isolation.

scan_loop and upload_loop run concurrently via asyncio.gather in main().
These tests verify that an exception raised inside one loop's body never
stops or crashes the other loop — each loop catches its own exceptions
and keeps retrying on the next cycle (see main.py docstrings).

A fake ruuvitag_sensor module is injected into sys.modules before
importing main/scanner, so these tests run without the real BLE library
(which requires Linux-only bluepy and isn't installed on Windows/CI).
"""

import asyncio
import logging
import logging.handlers
import os
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# --- Inject a fake ruuvitag_sensor so `import scanner` (via main) succeeds
# without the real BLE dependency. ---
if "ruuvitag_sensor" not in sys.modules:
    fake_pkg = types.ModuleType("ruuvitag_sensor")
    fake_ruuvi_mod = types.ModuleType("ruuvitag_sensor.ruuvi")

    class _FakeRuuviTagSensor:
        @staticmethod
        async def get_data_for_sensors_async(macs, timeout):
            return {}

        @staticmethod
        async def download_history(mac, start_time=None):
            return []

    fake_ruuvi_mod.RuuviTagSensor = _FakeRuuviTagSensor
    sys.modules["ruuvitag_sensor"] = fake_pkg
    sys.modules["ruuvitag_sensor.ruuvi"] = fake_ruuvi_mod

import main  # noqa: E402
from settings import Settings, SettingsStore  # noqa: E402
from storage import Storage  # noqa: E402
from mock_ruuvi import MAC_1, make_record  # noqa: E402


async def _run_briefly(coro, timeout=0.3):
    """Run an infinite loop coroutine for a short time, then cancel it."""
    task = asyncio.create_task(coro)
    await asyncio.sleep(timeout)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def tmp_storage(tmp_path, max_entries=100):
    return Storage(max_entries_per_tag=max_entries, db_path=str(tmp_path / "t.db"))


class FakeSettingsStore:
    """Minimal stand-in for SettingsStore returning a fixed Settings snapshot.

    Avoids touching disk/settings.local.json in loop-isolation tests –
    scan_loop/upload_loop only ever call ``await settings_store.get()``.
    """

    def __init__(self, **overrides):
        self._settings = Settings(**overrides)

    async def get(self) -> Settings:
        return self._settings


@pytest.mark.asyncio
async def test_scan_loop_survives_repeated_scan_failures(tmp_path):
    """scan_loop must keep looping (not crash) if scan() keeps raising."""
    settings_store = FakeSettingsStore(scan_interval_seconds=0)
    storage = tmp_storage(tmp_path)

    call_count = 0

    async def failing_scan(settings):
        nonlocal call_count
        call_count += 1
        raise RuntimeError("simulated BLE failure")

    with patch("main.scan", new=failing_scan):
        await _run_briefly(main.scan_loop(settings_store, storage, asyncio.Lock()))

    # The loop kept retrying instead of dying on the first exception.
    assert call_count >= 2


@pytest.mark.asyncio
async def test_scan_loop_skips_when_scan_disabled(tmp_path):
    """scan_loop must not call scanner when scan_enabled is false."""
    settings_store = FakeSettingsStore(scan_enabled=False, scan_interval_seconds=0)
    storage = tmp_storage(tmp_path)

    scan_calls = 0

    async def should_not_run(settings):
        nonlocal scan_calls
        scan_calls += 1
        return [make_record(mac=MAC_1)]

    with patch("main.scan", new=should_not_run):
        await _run_briefly(main.scan_loop(settings_store, storage, asyncio.Lock()))

    assert scan_calls == 0
    assert await storage.get_all() == []


@pytest.mark.asyncio
async def test_upload_loop_survives_uploader_exception(tmp_path):
    """upload_loop must keep looping if Uploader.upload() raises unexpectedly."""
    settings_store = FakeSettingsStore(
        upload_interval_seconds=0, cloud_upload_enabled=True
    )
    storage = tmp_storage(tmp_path)
    await storage.add(MAC_1, make_record(mac=MAC_1))

    uploader = AsyncMock()
    uploader.upload.side_effect = RuntimeError("simulated upload crash")

    await _run_briefly(main.upload_loop(settings_store, storage, uploader))

    # Upload kept being retried (pending record was never dropped since
    # upload never returned True), proving the loop didn't die.
    assert uploader.upload.call_count >= 2
    assert len(await storage.get_pending()) == 1


@pytest.mark.asyncio
async def test_scan_loop_unaffected_by_upload_loop_crashing():
    """The two loops are independent: a crashing upload_loop must not stop scan_loop."""
    scan_settings_store = FakeSettingsStore(scan_interval_seconds=0)
    upload_settings_store = FakeSettingsStore(
        upload_interval_seconds=0, cloud_upload_enabled=True
    )
    # This checks loop coordination, independent of SQLite/disk throughput.
    storage = AsyncMock(spec=Storage)
    storage.get_pending.return_value = [make_record(mac=MAC_1)]
    storage.tag_count.return_value = 1
    upload_retried = asyncio.Event()
    scan_after_upload_failures = asyncio.Event()
    uploader = AsyncMock()

    async def failing_upload(records):
        if uploader.upload.await_count >= 2:
            upload_retried.set()
        raise RuntimeError("simulated upload crash")

    async def working_scan(settings):
        if upload_retried.is_set():
            scan_after_upload_failures.set()
        return [make_record(mac=MAC_1)]

    uploader.upload.side_effect = failing_upload
    with patch("main.scan", new=working_scan):
        tasks = [
            asyncio.create_task(main.scan_loop(scan_settings_store, storage, asyncio.Lock())),
            asyncio.create_task(main.upload_loop(upload_settings_store, storage, uploader)),
        ]
        try:
            # Observe a scan after repeated upload failures rather than
            # assuming both loops can finish enough work in 0.3 seconds.
            await asyncio.wait_for(scan_after_upload_failures.wait(), timeout=5)
        finally:
            for task in tasks:
                task.cancel()
            results = await asyncio.gather(*tasks, return_exceptions=True)

    assert all(isinstance(result, asyncio.CancelledError) for result in results)
    assert uploader.upload.await_count >= 2
    storage.clear_pending.assert_not_awaited()


# ----------------------------------------------------------------------
# History backfill loop: BLE serialization and scheduling
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scan_loop_and_history_backfill_loop_never_overlap_ble_access(tmp_path):
    """The shared ble_lock must fully serialize scan_loop and
    history_backfill_loop's BLE access -- neither may be "inside" its BLE
    call (scan()/run_backfill_for_known_tags()) while the other is too.

    Each fake BLE call flips an "active" flag, sleeps briefly (giving the
    event loop a chance to switch to the sibling task if the lock isn't
    actually held for the whole call), then flips the flag back off,
    checking for the sibling's flag both before and after the sleep.
    """
    ble_lock = asyncio.Lock()
    scan_settings_store = FakeSettingsStore(scan_interval_seconds=0)
    backfill_settings_store = FakeSettingsStore(
        history_backfill_enabled=True,
        history_backfill_on_startup=False,
        history_backfill_periodic=True,
        history_backfill_interval_seconds=0,
    )
    storage = tmp_storage(tmp_path)

    active = {"scan": False, "backfill": False}
    overlap_detected = False

    async def tracking_scan(settings):
        nonlocal overlap_detected
        if active["backfill"]:
            overlap_detected = True
        active["scan"] = True
        await asyncio.sleep(0.01)
        if active["backfill"]:
            overlap_detected = True
        active["scan"] = False
        return []

    async def tracking_backfill(settings, storage, settings_store):
        nonlocal overlap_detected
        if active["scan"]:
            overlap_detected = True
        active["backfill"] = True
        await asyncio.sleep(0.01)
        if active["scan"]:
            overlap_detected = True
        active["backfill"] = False

    with patch("main.scan", new=tracking_scan), patch(
        "main.run_backfill_for_known_tags", new=tracking_backfill
    ):
        await asyncio.wait_for(
            asyncio.gather(
                _run_briefly(main.scan_loop(scan_settings_store, storage, ble_lock), timeout=0.3),
                _run_briefly(
                    main.history_backfill_loop(backfill_settings_store, storage, ble_lock),
                    timeout=0.3,
                ),
            ),
            timeout=2,
        )

    assert not overlap_detected


@pytest.mark.asyncio
async def test_history_backfill_loop_runs_once_on_startup(tmp_path):
    """With only history_backfill_on_startup set, the loop must run the
    backfill exactly once and then idle-poll (not repeat every cycle)."""
    settings_store = FakeSettingsStore(
        history_backfill_enabled=True,
        history_backfill_on_startup=True,
        history_backfill_periodic=False,
    )
    storage = tmp_storage(tmp_path)
    call_count = 0

    async def fake_backfill(settings, storage, settings_store):
        nonlocal call_count
        call_count += 1

    with patch("main.run_backfill_for_known_tags", new=fake_backfill):
        await _run_briefly(
            main.history_backfill_loop(settings_store, storage, asyncio.Lock()),
            timeout=0.3,
        )

    assert call_count == 1


@pytest.mark.asyncio
async def test_history_backfill_loop_runs_periodically(tmp_path):
    """With history_backfill_periodic set and a zero interval, the loop
    must keep re-running the backfill on every cycle."""
    settings_store = FakeSettingsStore(
        history_backfill_enabled=True,
        history_backfill_on_startup=False,
        history_backfill_periodic=True,
        history_backfill_interval_seconds=0,
    )
    storage = tmp_storage(tmp_path)
    call_count = 0

    async def fake_backfill(settings, storage, settings_store):
        nonlocal call_count
        call_count += 1

    with patch("main.run_backfill_for_known_tags", new=fake_backfill):
        await _run_briefly(
            main.history_backfill_loop(settings_store, storage, asyncio.Lock()),
            timeout=0.3,
        )

    assert call_count >= 2


# ----------------------------------------------------------------------
# File logging
# ----------------------------------------------------------------------

def test_configure_file_logging_creates_log_file_and_writes_to_it(tmp_path):
    log_path = str(tmp_path / "sub" / "ruuvihub.log")
    root_logger = logging.getLogger()
    handlers_before = list(root_logger.handlers)
    try:
        main.configure_file_logging(log_path)
        assert os.path.exists(log_path)

        test_logger = logging.getLogger("test_configure_file_logging")
        test_logger.setLevel(logging.INFO)
        test_logger.info("hello from test")
        for handler in root_logger.handlers:
            handler.flush()

        with open(log_path, "r", encoding="utf-8") as f:
            contents = f.read()
        assert "hello from test" in contents
    finally:
        # Restore the root logger's handlers so this test doesn't leak a
        # file handler (and its open file) into other tests.
        for handler in list(root_logger.handlers):
            if handler not in handlers_before:
                root_logger.removeHandler(handler)
                handler.close()


def test_configure_file_logging_uses_default_path_when_blank(tmp_path, monkeypatch):
    # Point the default path (Path(__file__).parent.parent / "ruuvihub.log")
    # at a temp dir by monkeypatching main._DEFAULT_LOG_PATH directly, since
    # the real default lives next to the actual repo and shouldn't be
    # written to by tests.
    default_path = str(tmp_path / "ruuvihub.log")
    monkeypatch.setattr(main, "_DEFAULT_LOG_PATH", default_path)

    root_logger = logging.getLogger()
    handlers_before = list(root_logger.handlers)
    try:
        main.configure_file_logging("")
        assert os.path.exists(default_path)
    finally:
        for handler in list(root_logger.handlers):
            if handler not in handlers_before:
                root_logger.removeHandler(handler)
                handler.close()


def test_configure_file_logging_uses_logs_subfolder_by_default():
    assert Path(main._DEFAULT_LOG_PATH).parent.name == "logs"


def test_configure_file_logging_rotates_daily(tmp_path):
    log_path = str(tmp_path / "ruuvihub.log")
    root_logger = logging.getLogger()
    handlers_before = list(root_logger.handlers)
    try:
        main.configure_file_logging(log_path)
        new_handlers = [h for h in root_logger.handlers if h not in handlers_before]
        assert len(new_handlers) == 1
        assert isinstance(new_handlers[0], logging.handlers.TimedRotatingFileHandler)
        assert new_handlers[0].when.lower() == "midnight"
        assert new_handlers[0].backupCount == main._LOG_BACKUP_COUNT
    finally:
        for handler in list(root_logger.handlers):
            if handler not in handlers_before:
                root_logger.removeHandler(handler)
                handler.close()


# ----------------------------------------------------------------------
# Startup log_path migration (guards against installs bootstrapped
# before daily-rotating logs lived under logs/ -- see the function's
# own docstring in main.py for why this runs here rather than in
# scripts/update.sh)
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_migrate_log_path_moves_flat_file_into_logs_subfolder(tmp_path, monkeypatch):
    old_log_path = tmp_path / "ruuvihub.log"
    old_log_path.write_text("old log contents", encoding="utf-8")
    (tmp_path / "ruuvihub.log.2026-08-27").write_text("rotated sibling", encoding="utf-8")
    monkeypatch.setattr(main, "_LEGACY_FLAT_LOG_PATH", str(old_log_path))
    settings_store = SettingsStore(settings_path=str(tmp_path / "settings.local.json"))
    await settings_store.update(log_path=str(old_log_path))

    new_log_path = await main._migrate_log_path_to_logs_subfolder(
        settings_store, str(old_log_path)
    )

    expected_new_path = tmp_path / "logs" / "ruuvihub.log"
    assert new_log_path == str(expected_new_path)
    assert expected_new_path.exists()
    assert expected_new_path.read_text(encoding="utf-8") == "old log contents"
    assert (tmp_path / "logs" / "ruuvihub.log.2026-08-27").exists()
    assert not old_log_path.exists()

    settings = await settings_store.get()
    assert settings.log_path == str(expected_new_path)


@pytest.mark.asyncio
async def test_migrate_log_path_is_noop_when_already_under_logs(tmp_path, monkeypatch):
    already_migrated = tmp_path / "logs" / "ruuvihub.log"
    already_migrated.parent.mkdir(parents=True)
    already_migrated.write_text("contents", encoding="utf-8")
    monkeypatch.setattr(main, "_LEGACY_FLAT_LOG_PATH", str(tmp_path / "ruuvihub.log"))
    settings_store = SettingsStore(settings_path=str(tmp_path / "settings.local.json"))
    await settings_store.update(log_path=str(already_migrated))

    result = await main._migrate_log_path_to_logs_subfolder(
        settings_store, str(already_migrated)
    )

    assert result == str(already_migrated)
    assert already_migrated.exists()


@pytest.mark.asyncio
async def test_migrate_log_path_is_noop_when_log_path_blank(tmp_path):
    settings_store = SettingsStore(settings_path=str(tmp_path / "settings.local.json"))

    result = await main._migrate_log_path_to_logs_subfolder(settings_store, "")

    assert result == ""


@pytest.mark.asyncio
async def test_migrate_log_path_leaves_operator_custom_path_untouched(tmp_path, monkeypatch):
    """A path shape heuristic ("not already inside a logs/ folder") would
    wrongly treat any operator-supplied custom log_path -- e.g. a
    dedicated /var/log-style location -- as the legacy default and
    silently move it. Only the exact known former default path may be
    migrated; anything else must be a true no-op, every time the app
    starts, regardless of the parent directory's name.
    """
    custom_path = tmp_path / "var_log" / "ruuvihub-custom.log"
    custom_path.parent.mkdir(parents=True)
    custom_path.write_text("custom contents", encoding="utf-8")
    monkeypatch.setattr(main, "_LEGACY_FLAT_LOG_PATH", str(tmp_path / "ruuvihub.log"))
    settings_store = SettingsStore(settings_path=str(tmp_path / "settings.local.json"))
    await settings_store.update(log_path=str(custom_path))

    result = await main._migrate_log_path_to_logs_subfolder(settings_store, str(custom_path))

    assert result == str(custom_path)
    assert custom_path.exists()
    assert custom_path.read_text(encoding="utf-8") == "custom contents"
    assert not (custom_path.parent / "logs").exists()
    settings = await settings_store.get()
    assert settings.log_path == str(custom_path)



@pytest.mark.asyncio
@pytest.mark.parametrize("status", [201, 403])
async def test_azure_upload_loop_acknowledges_only_success_and_survives_restart(tmp_path, status):
    import requests
    from uploader import Uploader
    store = FakeSettingsStore(cloud_upload_enabled=True, cloud_upload_destination="azure_blob",
        azure_blob_container_url="https://example.blob.core.windows.net/data?sr=c&sig=fake", upload_interval_seconds=1)
    storage = tmp_storage(tmp_path)
    await storage.add(MAC_1, make_record())
    response = requests.Response()
    response.status_code = status
    with patch("uploader.requests.put", return_value=response), patch("main.asyncio.sleep", new=AsyncMock(side_effect=[None, asyncio.CancelledError])):
        with pytest.raises(asyncio.CancelledError):
            await main.upload_loop(store, storage, Uploader(store))
    restarted = tmp_storage(tmp_path)
    assert len(await restarted.get_pending()) == (0 if status == 201 else 1)
    assert len(await restarted.get_history(MAC_1)) == 1
