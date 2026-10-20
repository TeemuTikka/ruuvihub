"""RuuviHub – main entry point.

Runs two async loops:
  - scan_loop: scans Ruuvi tags and stores readings in memory
  - upload_loop: uploads stored readings to the cloud (stub)

Both loops read their configuration from a shared SettingsStore at the
top of each iteration rather than a static snapshot taken once at
startup, so changes saved via the web config page (scan/upload
intervals, tag whitelist, retention, max_entries_per_tag,
cloud_upload_enabled, ...) take effect on the next cycle without
restarting the service. ``db_path`` is the one exception: it is read
once at startup only, since changing it requires fully re-initializing
Storage (see settings.py for details).
"""

import asyncio
import logging
import logging.handlers
import shutil
import sys
from pathlib import Path

from scanner import scan
from settings import SettingsStore
from storage import Storage
from uploader import Uploader
from history_backfill import initialize_pre_scan_backfill_cursors, run_backfill_for_known_tags
from web.app import run_web_server

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

_DEFAULT_LOG_PATH = str(Path(__file__).parent.parent / "logs" / "ruuvihub.log")
_LOG_BACKUP_COUNT = 14  # keep 14 days of daily-rotated log files

# The exact flat-file path used by every release before daily-rotating
# logs moved under logs/ (see b426768). Only this known former default is
# eligible for the startup migration below -- an operator-supplied custom
# log_path (e.g. /var/log/ruuvihub.log) must never be silently rewritten.
_LEGACY_FLAT_LOG_PATH = str(Path(__file__).parent.parent / "ruuvihub.log")


def configure_file_logging(log_path: str = "") -> None:
    """Attach a daily-rotating file handler to the root logger.

    Called once at startup (after settings are loaded) so scan/upload/web
    server logs are captured on disk in addition to stdout/console/journal.
    Rotates at midnight (local time), giving each calendar day its own log
    file under the ``logs/`` folder and keeping 14 days of history, so the
    log directory can't grow unbounded. ``log_path`` is file-only (like
    ``db_path``) — changing it requires a service restart, it is never
    live-reloaded.
    """
    resolved_path = Path(log_path) if log_path else Path(_DEFAULT_LOG_PATH)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.TimedRotatingFileHandler(
        resolved_path,
        when="midnight",
        backupCount=_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logging.getLogger().addHandler(file_handler)
    logger.info("File logging enabled: %s (rotates daily)", resolved_path)


async def _migrate_log_path_to_logs_subfolder(settings_store: SettingsStore, log_path: str) -> str:
    """Move the legacy flat default log file into a ``logs/`` subfolder.

    Installs bootstrapped before daily-rotating logs lived under ``logs/``
    have the old flat default ``log_path`` baked into
    ``settings.local.json`` (e.g. ``.../shared/ruuvihub.log``), and that
    file is otherwise never rewritten automatically. Doing this migration
    here -- at application startup, unconditionally, as a cheap no-op once
    already migrated -- means it takes effect on the very release that
    introduces ``logs/``, regardless of how the service was
    deployed/updated. (A shell-script migration step in
    ``scripts/update.sh`` can't offer that guarantee: the documented
    update flow runs the *previously installed* ``shared/scripts/update.sh``,
    which doesn't contain this release's migration logic yet, so a
    script-only migration would only take effect starting with the *next*
    update after this one.)

    Only the exact known former default path (``_LEGACY_FLAT_LOG_PATH``)
    is migrated. Any other configured ``log_path`` -- including an
    operator-supplied custom location such as ``/var/log/ruuvihub.log``
    -- is left untouched, since a path-shape heuristic ("not already
    inside a ``logs`` folder") would incorrectly treat those as legacy
    and silently move them too.

    Returns the (possibly updated) log_path to use for this run.
    """
    if not log_path:
        return log_path  # empty -- already using the logs/ default

    old_path = Path(log_path)
    if str(old_path) != _LEGACY_FLAT_LOG_PATH:
        return log_path  # not the known legacy default -- leave custom paths alone

    new_path = old_path.parent / "logs" / old_path.name
    new_path.parent.mkdir(parents=True, exist_ok=True)
    if old_path.parent.exists():
        # Move the old file and any already-rotated siblings (e.g.
        # ruuvihub.log.2026-08-27) into the new logs/ directory.
        for candidate in old_path.parent.glob(f"{old_path.name}*"):
            if candidate.is_file():
                shutil.move(str(candidate), str(new_path.parent / candidate.name))

    await settings_store.update(log_path=str(new_path))
    logger.info("Migrated log_path from %s to %s", old_path, new_path)
    return str(new_path)


async def scan_loop(settings_store: SettingsStore, storage: Storage, ble_lock: asyncio.Lock) -> None:
    """Periodically scan for Ruuvi tags and store readings.

    Reads live settings each iteration (scan interval/timeout, tag
    whitelist, max_entries_per_tag) so changes made via the web config
    page apply on the next cycle without a restart.

    ``ble_lock`` is shared with ``history_backfill_loop``: passive
    advertisement scanning and a GATT history pull cannot safely run at
    the same time on a single BLE radio (no lock inside the
    ruuvitag-sensor library protects this -- see
    README-dev.md's BLE radio serialization section). If backfill currently holds
    the lock, this cycle's scan is skipped entirely (not waited for,
    since scans are frequent and cheap to just retry next cycle).

    Any unexpected exception is logged and swallowed so a bug here never
    cancels or crashes upload_loop (asyncio.gather cancels all siblings
    the moment any task raises) — the loop just retries next cycle.
    """
    while True:
        settings = await settings_store.get()
        try:
            await storage.set_max_entries_per_tag(settings.max_entries_per_tag)
            if settings.scan_enabled:
                if ble_lock.locked():
                    logger.debug("BLE radio busy with history backfill – skipping this scan cycle")
                else:
                    # Acquiring the (currently free) lock here does not yield
                    # control back to the event loop, so there is no gap
                    # between the ``locked()`` check above and taking
                    # ownership of the radio. Holding the lock for the whole
                    # scan (not just the check) is what actually prevents
                    # history_backfill_loop from starting a GATT connection
                    # while a scan is still in flight.
                    async with ble_lock:
                        records = await scan(settings)
                        for record in records:
                            await storage.add(record["mac"], record)
                        count = await storage.tag_count()
                        logger.info("Storage now holds readings from %d unique tag(s)", count)
            else:
                logger.debug("Scanning disabled – skipping scan this cycle")
        except Exception:
            logger.exception("scan_loop iteration failed – will retry next cycle")
        await asyncio.sleep(settings.scan_interval_seconds)


async def upload_loop(
    settings_store: SettingsStore, storage: Storage, uploader: Uploader
) -> None:
    """Periodically upload pending readings to the cloud.

    Reads live settings each iteration (upload interval, retention,
    cloud_upload_enabled) so changes made via the web config page apply
    on the next cycle without a restart.

    Only readings not yet confirmed-uploaded are sent (Storage.get_pending),
    and they are only cleared from the queue after a successful upload, so
    a failed upload is retried on the next cycle instead of being dropped
    or re-sent forever. Uploads are skipped entirely while
    cloud_upload_enabled is off (the default) — the pending queue simply
    remains in SQLite (subject to configured history retention) until the user turns
    uploading on from the config page.

    Any unexpected exception is logged and swallowed so a bug here never
    cancels or crashes scan_loop (asyncio.gather cancels all siblings the
    moment any task raises) — the loop just retries next cycle.
    """
    initial_settings = await settings_store.get()
    await asyncio.sleep(initial_settings.upload_interval_seconds)  # initial delay
    while True:
        settings = await settings_store.get()
        try:
            if settings.cloud_upload_enabled:
                records = await storage.get_pending()
                if records:
                    success = await uploader.upload(records)
                    if success:
                        await storage.clear_pending(records)
            else:
                logger.debug("Cloud upload disabled – skipping upload this cycle")
            await storage.prune(settings.db_retention_days)
        except Exception:
            logger.exception("upload_loop iteration failed – will retry next cycle")
        await asyncio.sleep(settings.upload_interval_seconds)


async def history_backfill_loop(
    settings_store: SettingsStore, storage: Storage, ble_lock: asyncio.Lock
) -> None:
    """Periodically pull GATT history to backfill gaps in stored readings.

    Enabled by default (startup pass; ``history_backfill_enabled`` and
    ``history_backfill_on_startup`` default to ``True``). When enabled:
      - if ``history_backfill_on_startup`` is set, runs one pass the first
        time this loop sees the setting enabled;
      - if ``history_backfill_periodic`` is set, keeps re-running every
        ``history_backfill_interval_seconds``.
    Both can be on at once. If neither startup-run-once nor periodic apply
    on a given cycle, the loop just idle-polls every 60s so a live toggle
    of these settings from the Config page takes effect without a restart.

    ``ble_lock`` is shared with ``scan_loop``: a GATT history pull cannot
    safely run at the same time as passive advertisement scanning on a
    single BLE radio. Unlike ``scan_loop`` (which skips its cycle if the
    lock is held), this loop *waits* for the lock -- scans are quick
    (bounded by ``scan_timeout_seconds``), so a short wait here is cheaper
    than skipping an entire backfill cycle (which, by default, only runs
    hourly).

    Any unexpected exception is logged and swallowed so a bug here never
    cancels or crashes the other loops (asyncio.gather cancels all
    siblings the moment any task raises) — the loop just retries next
    cycle.
    """
    ran_startup = False
    while True:
        settings = await settings_store.get()
        try:
            if settings.history_backfill_enabled:
                did_run = False
                if not ran_startup and settings.history_backfill_on_startup:
                    async with ble_lock:
                        await run_backfill_for_known_tags(settings, storage, settings_store)
                    ran_startup = True
                    did_run = True
                if settings.history_backfill_periodic:
                    if not did_run:
                        async with ble_lock:
                            await run_backfill_for_known_tags(settings, storage, settings_store)
                    await asyncio.sleep(settings.history_backfill_interval_seconds)
                    continue
        except Exception:
            logger.exception("history_backfill_loop iteration failed – will retry next cycle")
        await asyncio.sleep(60)


async def web_server_loop(
    storage: Storage, settings_store: SettingsStore, ble_lock: asyncio.Lock
) -> None:
    """Run the embedded web UI, restarting it if it ever exits unexpectedly.

    Wrapped in the same retry-and-log pattern as scan_loop/upload_loop so a
    crash in the web server can't cancel or crash the scan/upload loops via
    asyncio.gather, and the UI comes back automatically after a short
    delay instead of staying down for the rest of the process's life.

    ``ble_lock`` is passed through to the web app so the Config page's
    "Sync history now" button can trigger an immediate manual backfill
    that still serializes with scan_loop's BLE radio access.
    """
    while True:
        try:
            await run_web_server(storage, settings_store, ble_lock)
            logger.warning("Web server exited unexpectedly – restarting in 5s")
        except Exception:
            logger.exception("Web server crashed – restarting in 5s")
        await asyncio.sleep(5)


async def main() -> None:
    settings_store = SettingsStore()
    settings = await settings_store.get()
    log_path = await _migrate_log_path_to_logs_subfolder(settings_store, settings.log_path)
    configure_file_logging(log_path)
    logger.info(
        "RuuviHub starting – scan every %ds, upload every %ds (cloud upload %s)",
        settings.scan_interval_seconds,
        settings.upload_interval_seconds,
        "enabled" if settings.cloud_upload_enabled else "disabled",
    )

    storage = Storage(
        max_entries_per_tag=settings.max_entries_per_tag,
        db_path=settings.db_path or None,
        retention_days=settings.db_retention_days,
    )
    uploader = Uploader(settings_store)
    ble_lock = asyncio.Lock()

    # Must run before scan_loop's first iteration (see the function's own
    # docstring): seeds each known tag's backfill cursor from its current
    # latest live-scan reading so the very first backfill pass has an
    # accurate pre-outage-recovery starting point, instead of one already
    # contaminated by readings scan_loop is about to write.
    await initialize_pre_scan_backfill_cursors(settings, storage)

    # return_exceptions=True is defense-in-depth: scan_loop/upload_loop/
    # web_server_loop already catch their own exceptions internally, but
    # this ensures that even a truly unexpected escape (e.g. a bug in an
    # except block itself) can't cancel a sibling task or crash the whole
    # process.
    results = await asyncio.gather(
        scan_loop(settings_store, storage, ble_lock),
        upload_loop(settings_store, storage, uploader),
        history_backfill_loop(settings_store, storage, ble_lock),
        web_server_loop(storage, settings_store, ble_lock),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, Exception):
            logger.error("A loop exited unexpectedly: %s", result)


if __name__ == "__main__":
    asyncio.run(main())
