"""GATT history backfill for Ruuvi tags.

RuuviTag firmware 3.x (>= 3.30.0) maintains a rolling on-device flash log
of temperature/humidity/pressure, retrievable over a GATT (Bluetooth
connection) channel independent of the passive BLE advertisements that
``scanner.py`` normally reads. This module wraps
``RuuviTagSensor.download_history()`` (from the already-used
``ruuvitag-sensor`` dependency) to pull that log and reconstruct it into
records shaped like normal live-scan rows, so gaps caused by a Pi/BLE
outage can be partially filled in.

See README-dev.md's GATT history backfill section and protocol references
for the technical background. This cannot recover gaps beyond the tag's
on-device log depth (~12 days at firmware defaults) and why it cannot
run concurrently with passive scanning on the same BLE radio (no
library-level lock protects that; see ``main.py``'s shared ``ble_lock``).

Each GATT history entry only ever carries ONE of temperature/humidity/
pressure plus its own Unix timestamp -- the RuuviTag sends each
measurement type as a separate log entry, and per the ``ruuvitag-sensor``
library's own docstring, callers must merge them by timestamp themselves.
Timestamps for the temperature/humidity/pressure of "the same" sample are
not guaranteed to be bit-identical, so entries are bucketed into a small
tolerance window (rounded to the nearest ``_BUCKET_SECONDS``) before being
merged into one record.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ruuvitag_sensor.ruuvi import RuuviTagSensor

logger = logging.getLogger(__name__)

# Bucket width (seconds) used to group temperature/humidity/pressure log
# entries that belong to "the same" sample despite carrying slightly
# different timestamps. Matches the granularity of the tag's default
# ~5 minute logging interval -- comfortably wider than the couple of
# seconds' jitter typically seen between the three components, without
# risking merging two genuinely distinct samples together.
_BUCKET_SECONDS = 10

# Our own per-tag timeout default for a history pull. Deliberately
# shorter than the ruuvitag-sensor library's own 300s default: a single
# unreachable or very-far-away tag must not be able to stall an entire
# backfill cycle (and, transitively, hold the shared BLE lock)
# indefinitely. Overridable via Settings.history_backfill_timeout_seconds
# (a separate, independent setting from scan_timeout_seconds, which only
# governs quick passive-advertisement scan windows) -- a history pull can
# legitimately take a minute or two for a tag holding close to its full
# on-device log, so this default is deliberately much larger than
# scan_timeout_seconds's.
_DEFAULT_PER_TAG_TIMEOUT_SECONDS = 120


def merge_history_entries(entries: List[dict]) -> List[dict]:
    """Merge split temperature/humidity/pressure entries into combined records.

    Each input entry is a ``SensorHistoryData``-shaped dict with exactly
    one of ``temperature``/``humidity``/``pressure`` populated (the others
    ``None``) and an integer Unix ``timestamp``. Entries are sorted by
    timestamp and greedily grouped: an entry joins the current group if it
    falls within ``_BUCKET_SECONDS`` of that group's *starting* timestamp
    AND the group doesn't already have a value for that measurement type
    (a repeated field means it belongs to the next sample, not this one).
    Grouping by proximity to a common anchor (rather than independently
    rounding each timestamp to the nearest fixed-width bucket) avoids
    splitting genuinely-close entries that happen to straddle a bucket
    boundary (e.g. offsets 4s and 6s, only 2s apart, previously rounded
    into two different buckets and produced two incomplete records).

    Returns a list of dicts with keys ``timestamp`` (ISO-8601 UTC string),
    and any of ``temperature``/``humidity``/``pressure`` that were present,
    ordered oldest-to-newest.
    """
    valid_entries = [e for e in entries if e.get("timestamp") is not None]
    valid_entries.sort(key=lambda e: int(e["timestamp"]))

    groups: List[dict] = []  # each: {"start_ts": int, "fields": {...}}
    for entry in valid_entries:
        raw_ts = int(entry["timestamp"])
        current = groups[-1] if groups else None
        joins_current = False
        if current is not None and raw_ts - current["start_ts"] <= _BUCKET_SECONDS:
            has_conflict = any(
                entry.get(field) is not None and current["fields"].get(field) is not None
                for field in ("temperature", "humidity", "pressure")
            )
            joins_current = not has_conflict

        if not joins_current:
            current = {"start_ts": raw_ts, "fields": {}}
            groups.append(current)

        for field in ("temperature", "humidity", "pressure"):
            value = entry.get(field)
            if value is not None:
                current["fields"][field] = value

    records = []
    for group in groups:
        merged = dict(group["fields"])
        merged["timestamp"] = (
            datetime.fromtimestamp(group["start_ts"], tz=timezone.utc).isoformat()
        )
        records.append(merged)
    return records


class BackfillError(Exception):
    """Raised when a tag's GATT history pull fails (unreachable, timeout,
    firmware too old, etc.). Callers use this to distinguish a real
    failure (count towards the consecutive-failure threshold) from a
    successful pull that simply had no new data."""


async def backfill_tag(
    mac: str, since: Optional[str], timeout_seconds: int = _DEFAULT_PER_TAG_TIMEOUT_SECONDS
) -> List[dict]:
    """Pull and reconstruct GATT history for one tag.

    Args:
        mac: Tag MAC address.
        since: ISO-8601 timestamp of the last reading already stored for
            this tag (used as the GATT range's start and for dedup), or
            ``None`` to pull all available on-device history (fresh tag).
        timeout_seconds: Max seconds to wait for this tag's GATT pull
            before giving up (see ``Settings.history_backfill_timeout_seconds``).
            Defaults to ``_DEFAULT_PER_TAG_TIMEOUT_SECONDS`` for callers
            (e.g. tests) that don't have a live ``Settings`` to read from.

    Returns:
        List of merged records (see ``merge_history_entries``), each with
        ``mac`` added, filtered to strictly after ``since``, oldest first.
        An empty list is a *successful* call that found nothing new.

    Raises:
        BackfillError: if the GATT pull itself failed (BLE error, old
            firmware, timeout) -- callers use this to track consecutive
            per-tag failures towards the auto-disable threshold.
    """
    start_time = None
    if since is not None:
        try:
            start_time = datetime.fromisoformat(since)
        except ValueError:
            logger.warning("Could not parse stored timestamp %r for %s", since, mac)

    try:
        raw_entries = await asyncio.wait_for(
            RuuviTagSensor.download_history(mac, start_time=start_time),
            timeout=timeout_seconds,
        )
    except Exception as exc:
        logger.warning(
            "History backfill failed for %s (unreachable, or firmware < 3.30.0)",
            mac,
            exc_info=True,
        )
        raise BackfillError(f"History pull failed for {mac}") from exc

    if not raw_entries:
        return []

    merged = merge_history_entries(raw_entries)
    if since is not None:
        merged = [r for r in merged if r["timestamp"] > since]

    for record in merged:
        record["mac"] = mac

    logger.info("History backfill recovered %d record(s) for %s", len(merged), mac)
    return merged


# Per-mac consecutive-failure counters, seeded from
# Settings.history_backfill_failure_counts on each call so counts survive
# process restarts and permanently failing tags can still reach the auto-disable
# threshold without needing to fail multiple times in the same process lifetime.
_consecutive_failures: Dict[str, int] = {}


async def initialize_pre_scan_backfill_cursors(settings, storage) -> None:
    """Bootstrap each known tag's backfill cursor before scanning starts.

    Must be called once at process startup, before ``scan_loop`` runs its
    first iteration. A tag's backfill cursor (``Storage.get_backfill_cursor``)
    is only ever advanced by a completed backfill pass, but the very first
    time a tag is ever backfilled there is no cursor yet -- without this
    bootstrap, ``run_backfill_for_known_tags`` would fall back to "pull
    everything the tag's flash log has", re-importing history that
    overlaps with readings already stored from live scanning.

    Seeding the initial cursor from ``get_latest_timestamp`` here (before
    ``scan_loop`` has written anything new) captures the correct
    pre-outage-recovery state. If this ran *after* scanning resumed
    instead, the seeded cursor would already reflect fresh live data and
    the gap during the outage would be skipped -- the same bug this
    separate-cursor design is meant to avoid.
    """
    if settings.tag_addresses:
        candidate_macs = list(settings.tag_addresses)
    else:
        candidate_macs = await storage.known_macs()

    for mac in candidate_macs:
        if await storage.get_backfill_cursor(mac) is not None:
            continue
        latest = await storage.get_latest_timestamp(mac)
        if latest is not None:
            await storage.set_backfill_cursor(mac, latest)


async def run_backfill_for_known_tags(settings, storage, settings_store) -> None:
    """Run a full backfill pass over every eligible known tag, sequentially.

    Eligible tags are the configured whitelist (``settings.tag_addresses``)
    if set, else every mac ``storage`` currently knows about, minus any mac
    present in ``settings.history_backfill_disabled_tags``.

    Tags are processed one at a time (GATT connections are exclusive on a
    single BLE radio anyway). A tag's consecutive-failure counter resets to
    0 on any successful pull (even if it recovered zero new records) and
    increments on a ``BackfillError``; once it reaches
    ``settings.history_backfill_failure_threshold`` the tag is persisted
    into ``history_backfill_disabled_tags`` (via ``settings_store``) and
    skipped on every subsequent cycle until an admin clears it from the
    Config page.

    Uses ``storage``'s dedicated backfill cursor (not the latest live-scan
    reading) as the GATT range's lower bound -- see
    ``Storage.get_backfill_cursor`` for why the two must be kept separate.
    If no cursor is recorded yet for a mac (should be rare in practice
    since ``initialize_pre_scan_backfill_cursors`` seeds it at startup),
    falls back to the latest live-scan reading so a never-before-backfilled
    tag with existing history doesn't re-import everything from scratch.
    """
    disabled = settings.history_backfill_disabled_tags
    if settings.tag_addresses:
        candidate_macs = list(settings.tag_addresses)
    else:
        candidate_macs = await storage.known_macs()
    macs = [mac for mac in candidate_macs if mac not in disabled]

    # Seed from persisted counts so partial failure runs from previous processes
    # carry forward; don't overwrite in-session increments already in memory.
    for mac, count in settings.history_backfill_failure_counts.items():
        if mac not in _consecutive_failures:
            _consecutive_failures[mac] = count

    for mac in macs:
        since = await storage.get_backfill_cursor(mac)
        if since is None:
            since = await storage.get_latest_timestamp(mac)
        pull_started_at = datetime.now(tz=timezone.utc).isoformat()
        try:
            records = await backfill_tag(
                mac, since, timeout_seconds=settings.history_backfill_timeout_seconds
            )
        except BackfillError:
            _consecutive_failures[mac] = _consecutive_failures.get(mac, 0) + 1
            await settings_store.set_backfill_failure_count(mac, _consecutive_failures[mac])
            if _consecutive_failures[mac] >= settings.history_backfill_failure_threshold:
                logger.warning(
                    "Tag %s failed history backfill %d time(s) in a row -- "
                    "disabling it from backfill until re-enabled from the Config page",
                    mac,
                    _consecutive_failures[mac],
                )
                await settings_store.set_backfill_disabled(
                    mac, f"{_consecutive_failures[mac]} consecutive backfill failures"
                )
                _consecutive_failures.pop(mac, None)
                await settings_store.set_backfill_failure_count(mac, None)
            continue

        _consecutive_failures.pop(mac, None)
        await settings_store.set_backfill_failure_count(mac, None)
        for record in records:
            await storage.add(record["mac"], record)

        # The GATT pull covers the tag's whole on-device log up to "now",
        # so on success the cursor advances to when the pull *started*
        # (not "now" after processing, and not the newest record's own
        # timestamp) -- this is a small, deliberately-safe underestimate
        # that avoids a race where the device logs a new entry between the
        # pull starting and this line running, which would otherwise never
        # be picked up (starting the next pass's ``since`` strictly after
        # it). Only advanced on success, never on failure, so a failed
        # pull's gap is retried next time instead of silently skipped.
        await storage.set_backfill_cursor(mac, pull_started_at)

