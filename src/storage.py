"""Storage for Ruuvi tag readings.

Readings are kept in an in-memory ring buffer (fast reads) and also
persisted to a local SQLite database (survives restarts / power loss).

Database location is set via config db_path (default: ruuvi_data.db
next to main.py).
"""

import asyncio
import json
import logging
import math
import shutil
import sqlite3
from collections import deque
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Bound each upload batch in memory; the full pending queue lives in SQLite.
_MAX_PENDING_RECORDS = 10_000
TEMPERATURE_CHANGE_TOLERANCE_SECONDS = 600

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS readings (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    mac       TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    data      TEXT NOT NULL,
    uploaded  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_readings_mac ON readings(mac);
CREATE INDEX IF NOT EXISTS idx_readings_timestamp ON readings(timestamp);
CREATE INDEX IF NOT EXISTS idx_readings_mac_time ON readings(mac, timestamp, id);
CREATE TABLE IF NOT EXISTS backfill_cursors (
    mac       TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL
);
"""


class UploadBatch(list):
    """Upload payload with private acknowledgement IDs, excluded from JSON."""

    def __init__(self, rows):
        rows = list(rows)
        super().__init__(record for _, record in rows)
        self.record_ids = tuple(record_id for record_id, _ in rows)


class Storage:
    """In-memory ring buffer backed by a SQLite database.

    Two separate views are kept over the data:
      - ``_data``: a bounded per-tag ring buffer used for reads
        (``get_all`` / ``get_by_mac``), always reflecting the most recent
        ``max_entries_per_tag`` readings.
      - SQLite rows with ``uploaded = 0`` form a durable upload queue.
        Bounded payload snapshots are acknowledged by database ID only
        after successful delivery. Delivery is at least once.
    """

    def __init__(
        self,
        max_entries_per_tag: int = 100,
        db_path: Optional[str] = None,
        retention_days: int = 0,
    ) -> None:
        self._max_entries = max_entries_per_tag
        self._data: Dict[str, deque] = {}
        self._lock = asyncio.Lock()
        self._retention_days = retention_days

        if db_path is None:
            db_path = str(Path(__file__).parent.parent / "ruuvi_data.db")
        self._db_path = db_path

        self._init_db()
        self._load_from_db()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def add(self, mac: str, record: dict) -> None:
        """Add a reading for the given MAC address and persist it."""
        await asyncio.get_running_loop().run_in_executor(
            None, self._save_record, record
        )
        async with self._lock:
            records = [*self._data.get(mac, ()), record]
            records.sort(key=lambda reading: reading["timestamp"])
            self._data[mac] = deque(records[-self._max_entries:], maxlen=self._max_entries)

    async def get_all(self) -> List[dict]:
        """Return a flat list of all stored records across all tags."""
        async with self._lock:
            result = []
            for records in self._data.values():
                result.extend(records)
            return result

    async def get_latest_readings(self) -> List[dict]:
        """Return one latest measurement per tag from the sorted cache.

        Cache writes and startup loading order by timestamp, retaining
        insertion order for ties. Read under the same lock as updates and
        resizing; dashboard work then scales with tags, not buffer size.
        """
        async with self._lock:
            return [records[-1] for records in self._data.values() if records]

    async def get_storage_stats(self) -> dict:
        """Return aggregate database size, disk space, and reading timestamps."""
        return await asyncio.get_running_loop().run_in_executor(
            None, self._get_storage_stats_sync
        )

    async def get_temperature_summaries(
        self, macs: List[str], day_start: str, day_end: str,
        night_start: str, night_end: str, until: str,
        change_hours: int = 1,
    ) -> dict[str, dict]:
        """Aggregate dashboard temperatures without loading history into Python.

        Bounds are UTC ISO timestamps; extrema use half-open windows.
        All tags share one connection and read snapshot in one executor call.
        SQLite scans the selected windows but returns only summary values.
        Hourly comparisons select numeric temperatures within ten minutes
        of ``change_hours`` before ``until``, using second-resolution distance.
        """
        if not macs:
            return {}
        return await asyncio.get_running_loop().run_in_executor(
            None, self._get_temperature_summaries_sync, macs,
            day_start, day_end, night_start, night_end, until, change_hours,
        )

    async def get_by_mac(self, mac: str) -> List[dict]:
        """Return all stored records for a specific MAC address."""
        async with self._lock:
            return list(self._data.get(mac, []))

    async def tag_count(self) -> int:
        """Return the number of unique tags seen."""
        async with self._lock:
            return len(self._data)

    async def get_pending(self) -> UploadBatch:
        """Read a bounded payload snapshot from the durable upload queue."""
        return await asyncio.get_running_loop().run_in_executor(
            None, self._get_pending_sync
        )

    async def clear_pending(self, batch: UploadBatch) -> None:
        """Durably acknowledge only the successfully uploaded snapshot."""
        await asyncio.get_running_loop().run_in_executor(
            None, self._clear_pending_sync, batch.record_ids
        )

    def _get_pending_sync(self) -> UploadBatch:
        with closing(sqlite3.connect(self._db_path)) as conn, conn:
            rows = conn.execute(
                "SELECT id, data FROM readings WHERE uploaded = 0 ORDER BY id LIMIT ?",
                (_MAX_PENDING_RECORDS,),
            ).fetchall()
        return UploadBatch((record_id, json.loads(data)) for record_id, data in rows)

    def _clear_pending_sync(self, record_ids: tuple[int, ...]) -> None:
        with closing(sqlite3.connect(self._db_path)) as conn, conn:
            conn.executemany(
                "UPDATE readings SET uploaded = 1 WHERE id = ?",
                ((record_id,) for record_id in record_ids),
            )

    async def known_macs(self) -> List[str]:
        """Return every MAC address currently held in the in-memory buffer.

        Used by the Config page so tags discovered while scanning in
        "scan all" mode (empty whitelist) show up for friendly-naming even
        though they were never explicitly added to ``tag_addresses``.
        """
        async with self._lock:
            return list(self._data.keys())

    async def get_latest_timestamp(self, mac: str) -> Optional[str]:
        """Return the most recent stored ISO-8601 timestamp for ``mac``.

        Used only to bootstrap a tag's backfill cursor the first time it
        is ever backfilled (see ``get_backfill_cursor``/``set_backfill_cursor``)
        and for the Config/history pages. Returns ``None`` if nothing has
        ever been stored for this mac.
        """
        return await asyncio.get_running_loop().run_in_executor(
            None, self._get_latest_timestamp_sync, mac
        )

    async def get_backfill_cursor(self, mac: str) -> Optional[str]:
        """Return the persisted GATT history backfill cursor for ``mac``.

        This is deliberately tracked separately from
        ``get_latest_timestamp`` (the newest *live-scan* reading): scanning
        and backfill run independently, so live readings keep arriving and
        advancing ``get_latest_timestamp`` the moment BLE connectivity
        resumes -- using that as the backfill lower bound would make an
        already-completed outage gap look "caught up" and unrecoverable as
        soon as a single live advertisement is stored. The backfill cursor
        only ever advances when a backfill pass itself completes.
        Returns ``None`` if no cursor has been recorded yet (fresh tag or
        never backfilled before).
        """
        return await asyncio.get_running_loop().run_in_executor(
            None, self._get_backfill_cursor_sync, mac
        )

    async def set_backfill_cursor(self, mac: str, timestamp: str) -> None:
        """Persist the GATT history backfill cursor for ``mac``.

        Called after a backfill pass for this tag completes successfully,
        so the next pass (startup or periodic) resumes from here instead
        of from whatever the live scanner has written in the meantime.
        """
        await asyncio.get_running_loop().run_in_executor(
            None, self._set_backfill_cursor_sync, mac, timestamp
        )

    async def get_history_sampled(
        self,
        mac: str,
        since: Optional[str] = None,
        until: Optional[str] = None,
        max_points: int = 500,
    ) -> List[dict]:
        """Return persisted readings for ``mac``, downsampled in SQL.

        Unlike ``get_history`` (which always returns every matching row),
        this bounds both the amount of data selected/transferred/decoded
        *and* the amount retained in memory to roughly ``max_points`` rows,
        by counting matching rows first and only fetching every Nth one
        (always keeping the first and last) when the count exceeds the
        cap. This avoids materializing the full history in Python just to
        immediately discard most of it, which matters on the long ranges
        (30d/all) an unbounded-retention database can accumulate.
        """
        return await asyncio.get_running_loop().run_in_executor(
            None, self._get_history_sampled_sync, mac, since, until, max_points
        )

    async def get_history(
        self,
        mac: str,
        since: Optional[str] = None,
        until: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[dict]:
        """Return persisted readings for ``mac`` from the SQLite DB.

        Unlike ``get_by_mac`` (which reads the bounded in-memory ring
        buffer), this queries the full on-disk history directly, so it is
        used by the web UI's per-sensor history charts and CSV export.

        Args:
            mac: MAC address to fetch history for.
            since: Optional ISO-8601 timestamp (inclusive) lower bound.
            until: Optional ISO-8601 timestamp (inclusive) upper bound.
            limit: Optional cap on the number of rows returned (newest
                first before re-ordering); ``None`` returns everything in
                the range.

        Returns:
            List of record dicts ordered oldest→newest.
        """
        return await asyncio.get_running_loop().run_in_executor(
            None, self._get_history_sync, mac, since, until, limit
        )

    async def get_chart_history(
        self, mac: str, since: Optional[str], until: str,
        gap_seconds: int, max_points: int = 500,
    ) -> List[dict]:
        """Sample chart data while preserving gaps from the unsampled history.

        Each metric has a segment counter. A changed counter means a gap
        occurred between selected points, even if sampling omitted the
        missing values or made consecutive measurements look far apart.
        """
        return await asyncio.get_running_loop().run_in_executor(
            None, self._get_chart_history_sync, mac, since, until,
            gap_seconds, max_points,
        )

    def _get_chart_history_sync(self, mac, since, until, gap_seconds, max_points):
        fields = ("temperature", "humidity", "pressure", "battery")
        where = "WHERE mac = ? AND timestamp <= ?"
        params = [mac, until]
        if since is not None:
            where += " AND timestamp >= ?"
            params.append(since)
        # GATT backfill has no data_format and normally logs every five
        # minutes. Live advertisements use the configured scan cadence.
        # Pack missing-metric flags into one integer instead of carrying
        # the JSON payload through each window. Each bucket ends at a
        # sampled row; summing its gaps then accumulating bucket totals
        # gives exactly that row's original segment counters. Only the
        # final <= max_points rows join back to the JSON payloads.
        gap_counts = ", ".join(
            f"SUM(CASE WHEN time_gap OR (missing_mask & {1 << index}) THEN 1 ELSE 0 END) "
            f"AS {field}_gaps"
            for index, field in enumerate(fields)
        )
        segment_columns = ", ".join(
            f"SUM({field}_gaps) OVER (ORDER BY bucket) AS {field}_segment"
            for field in fields
        )
        missing_mask = " | ".join(
            f"((COALESCE(json_type(data, '$.{field}') IN ('integer', 'real'), 0) = 0) << {index})"
            for index, field in enumerate(fields)
        )
        with closing(sqlite3.connect(self._db_path)) as conn, conn:
            # Count and sample one snapshot even if ingestion is active.
            conn.execute("BEGIN")
            count = conn.execute(f"SELECT COUNT(*) FROM readings {where}", params).fetchone()[0]
            step = max(1, math.ceil(count / max(max_points - 1, 1))) if count > max_points else 1
            rows = conn.execute(f"""
                WITH ordered AS (
                    SELECT id, ({missing_mask}) AS missing_mask,
                           unixepoch(timestamp) - LAG(unixepoch(timestamp)) OVER (ORDER BY timestamp, id) AS elapsed_seconds,
                           json_extract(data, '$.data_format') IS NULL OR
                           LAG(json_extract(data, '$.data_format')) OVER (ORDER BY timestamp, id) IS NULL AS backfill_gap,
                           ROW_NUMBER() OVER (ORDER BY timestamp, id) - 1 AS rn
                    FROM readings {where}
                ), gaps AS (
                    SELECT *,
                           elapsed_seconds >
                           CASE WHEN backfill_gap
                                THEN MAX(?, 600) ELSE ? END AS time_gap
                    FROM ordered
                ), buckets AS (
                    SELECT (rn + ? - 1) / ? AS bucket,
                           MAX(CASE WHEN rn % ? = 0 OR rn = ? THEN id END) AS id,
                           {gap_counts}
                    FROM gaps GROUP BY bucket
                ), segmented AS (
                    SELECT id, bucket, {segment_columns} FROM buckets
                )
                SELECT readings.data, {', '.join(f'{field}_segment' for field in fields)}
                FROM segmented JOIN readings ON readings.id = segmented.id
                ORDER BY bucket
            """, [*params, gap_seconds, gap_seconds, step, step, step, count - 1]).fetchall()
        records = []
        for data, *segments in rows:
            record = json.loads(data)
            record["_chart_segments"] = dict(zip(fields, segments))
            records.append(record)
        return records

    async def prune(self, retention_days: Optional[int] = None) -> None:
        """Delete DB rows older than the retention window.

        Args:
            retention_days: Overrides the value passed at construction for
                this call, so callers can pass a freshly-read live setting
                each cycle without needing to reconstruct Storage.
                ``None`` falls back to the value set at construction time.
        """
        days = self._retention_days if retention_days is None else retention_days
        if days <= 0:
            return
        await asyncio.get_running_loop().run_in_executor(None, self._prune_db, days)

    async def set_max_entries_per_tag(self, new_max: int) -> None:
        """Live-resize the in-memory ring buffer capacity per tag.

        Existing readings are preserved (truncated to the newest
        ``new_max`` per tag if shrinking). This lets the web config page
        change ``max_entries_per_tag`` without restarting the service,
        unlike ``db_path`` which requires a full re-init.
        """
        async with self._lock:
            if new_max == self._max_entries:
                return
            self._max_entries = new_max
            for mac, buf in list(self._data.items()):
                self._data[mac] = deque(buf, maxlen=new_max)

    # ------------------------------------------------------------------
    # SQLite helpers (synchronous – run via executor from async callers)
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        with closing(sqlite3.connect(self._db_path)) as conn, conn:
            conn.executescript(_CREATE_TABLE)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(readings)")}
            if "uploaded" not in columns:
                # Legacy rows have unknown delivery status. Retry them rather
                # than silently lose readings that may never have been sent.
                conn.execute("ALTER TABLE readings ADD COLUMN uploaded INTEGER NOT NULL DEFAULT 0")
                logger.warning("Migrated upload queue: existing readings will be retried")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_readings_pending ON readings(id) WHERE uploaded = 0")
        logger.info("SQLite database ready: %s", self._db_path)

    def _get_storage_stats_sync(self) -> dict:
        """Read persisted reading bounds and file-system usage without exposing paths."""
        with closing(sqlite3.connect(self._db_path)) as conn, conn:
            count = conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
            oldest = conn.execute("SELECT MIN(timestamp) FROM readings").fetchone()[0]
            newest = conn.execute("SELECT MAX(timestamp) FROM readings").fetchone()[0]
        try:
            database_size = Path(self._db_path).stat().st_size
        except OSError:
            database_size = None
        try:
            disk_free = shutil.disk_usage(Path(self._db_path).resolve().parent).free
        except OSError:
            disk_free = None
        return {
            "reading_count": count,
            "oldest_timestamp": oldest,
            "newest_timestamp": newest,
            "database_size_bytes": database_size,
            "disk_free_bytes": disk_free,
        }

    def _save_record(self, record: dict) -> None:
        try:
            with closing(sqlite3.connect(self._db_path)) as conn, conn:
                conn.execute(
                    "INSERT INTO readings (mac, timestamp, data) VALUES (?, ?, ?)",
                    (record["mac"], record["timestamp"], json.dumps(record, default=str)),
                )
        except Exception:
            logger.exception("Failed to persist record for %s", record.get("mac"))
            raise

    def _get_temperature_summaries_sync(
        self, macs: List[str], day_start: str, day_end: str,
        night_start: str, night_end: str, until: str,
        change_hours: int = 1,
    ) -> dict[str, dict]:
        summaries = {}
        target = datetime.fromisoformat(until) - timedelta(hours=change_hours)
        tolerance = timedelta(seconds=TEMPERATURE_CHANGE_TOLERANCE_SECONDS)
        with closing(sqlite3.connect(self._db_path)) as conn, conn:
            conn.execute("BEGIN")
            for mac in macs:
                row = conn.execute("""
                    SELECT MIN(CASE WHEN timestamp >= ? AND timestamp < ? THEN temperature END),
                           MAX(CASE WHEN timestamp >= ? AND timestamp < ? THEN temperature END),
                           MIN(CASE WHEN timestamp >= ? AND timestamp < ? THEN temperature END),
                           MAX(CASE WHEN timestamp >= ? AND timestamp < ? THEN temperature END)
                    FROM (
                        SELECT timestamp,
                               CASE WHEN json_type(data, '$.temperature') IN ('integer', 'real')
                                    THEN json_extract(data, '$.temperature') END AS temperature
                        FROM readings WHERE mac = ? AND timestamp >= ? AND timestamp <= ?
                    )
                """, (
                    day_start, day_end, day_start, day_end,
                    night_start, night_end, night_start, night_end,
                    mac, min(day_start, night_start), until,
                )).fetchone()
                hourly = conn.execute("""
                    SELECT json_extract(data, '$.temperature')
                    FROM readings WHERE mac = ? AND timestamp >= ? AND timestamp <= ?
                    AND json_type(data, '$.temperature') IN ('integer', 'real')
                    ORDER BY ABS(unixepoch(timestamp) - unixepoch(?)), timestamp DESC, id DESC
                    LIMIT 1
                """, (mac, (target - tolerance).isoformat(),
                      (target + tolerance).isoformat(), target.isoformat())).fetchone()
                summaries[mac] = dict(zip((
                    "day_temperature_low", "day_temperature_high",
                    "night_temperature_low", "night_temperature_high",
                ), row))
                summaries[mac]["hour_temperature"] = hourly[0] if hourly else None
        return summaries

    def _get_history_sync(
        self,
        mac: str,
        since: Optional[str],
        until: Optional[str],
        limit: Optional[int],
    ) -> List[dict]:
        query = "SELECT data FROM readings WHERE mac = ?"
        params: List = [mac]
        if since is not None:
            query += " AND timestamp >= ?"
            params.append(since)
        if until is not None:
            query += " AND timestamp <= ?"
            params.append(until)
        query += " ORDER BY timestamp DESC, id DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)

        try:
            with closing(sqlite3.connect(self._db_path)) as conn, conn:
                rows = conn.execute(query, params).fetchall()
        except Exception:
            logger.exception("Failed to query history for %s", mac)
            return []

        # Rows are newest-first (needed for LIMIT to keep the most recent
        # readings); reverse so callers get oldest→newest for charting.
        return [json.loads(data_json) for (data_json,) in reversed(rows)]

    def _get_history_sampled_sync(
        self,
        mac: str,
        since: Optional[str],
        until: Optional[str],
        max_points: int,
    ) -> List[dict]:
        where = "WHERE mac = ?"
        params: List = [mac]
        if since is not None:
            where += " AND timestamp >= ?"
            params.append(since)
        if until is not None:
            where += " AND timestamp <= ?"
            params.append(until)

        try:
            with closing(sqlite3.connect(self._db_path)) as conn, conn:
                count = conn.execute(
                    f"SELECT COUNT(*) FROM readings {where}", params
                ).fetchone()[0]

                if count <= max_points:
                    rows = conn.execute(
                        f"SELECT data FROM readings {where} ORDER BY timestamp, id", params
                    ).fetchall()
                    return [json.loads(data_json) for (data_json,) in rows]

                # Keep roughly every Nth row (reserving one slot for the
                # final/newest row) so long-range charts stay small without
                # ever materializing the full history in Python.
                step = max(1, math.ceil(count / max(max_points - 1, 1)))
                query = f"""
                    WITH numbered AS (
                        SELECT id, data, ROW_NUMBER() OVER (ORDER BY timestamp, id) - 1 AS rn
                        FROM readings {where}
                    )
                    SELECT data FROM numbered
                    WHERE rn % ? = 0 OR rn = (SELECT MAX(rn) FROM numbered)
                    ORDER BY rn
                """
                rows = conn.execute(query, [*params, step]).fetchall()
                return [json.loads(data_json) for (data_json,) in rows]
        except Exception:
            logger.exception("Failed to query sampled history for %s", mac)
            return []

    def _get_latest_timestamp_sync(self, mac: str) -> Optional[str]:
        try:
            with closing(sqlite3.connect(self._db_path)) as conn, conn:
                # Order by the timestamp value itself, not insertion order
                # (``id``): backfilled historical rows can be inserted after
                # newer live rows already exist, so the last-inserted row is
                # not necessarily the most recent reading. ISO-8601 strings
                # sort correctly lexicographically.
                row = conn.execute(
                    "SELECT timestamp FROM readings WHERE mac = ? ORDER BY timestamp DESC LIMIT 1",
                    (mac,),
                ).fetchone()
                return row[0] if row else None
        except Exception:
            logger.exception("Failed to query latest timestamp for %s", mac)
            return None

    def _get_backfill_cursor_sync(self, mac: str) -> Optional[str]:
        try:
            with closing(sqlite3.connect(self._db_path)) as conn, conn:
                row = conn.execute(
                    "SELECT timestamp FROM backfill_cursors WHERE mac = ?",
                    (mac,),
                ).fetchone()
                return row[0] if row else None
        except Exception:
            logger.exception("Failed to query backfill cursor for %s", mac)
            return None

    def _set_backfill_cursor_sync(self, mac: str, timestamp: str) -> None:
        try:
            with closing(sqlite3.connect(self._db_path)) as conn, conn:
                conn.execute(
                    """
                    INSERT INTO backfill_cursors (mac, timestamp) VALUES (?, ?)
                    ON CONFLICT(mac) DO UPDATE SET timestamp = excluded.timestamp
                    """,
                    (mac, timestamp),
                )
        except Exception:
            logger.exception("Failed to persist backfill cursor for %s", mac)
            raise

    def _prune_db(self, retention_days: int) -> None:
        try:
            with closing(sqlite3.connect(self._db_path)) as conn, conn:
                cursor = conn.execute(
                    "DELETE FROM readings WHERE timestamp < datetime('now', ?)",
                    (f"-{retention_days} days",),
                )
                if cursor.rowcount:
                    logger.info(
                        "Pruned %d record(s) older than %d day(s)",
                        cursor.rowcount,
                        retention_days,
                    )
        except Exception:
            logger.exception("Failed to prune old records")

    def _load_from_db(self) -> None:
        """Populate the in-memory cache from the most recent DB entries.

        Loads the last ``max_entries_per_tag`` rows per known MAC address,
        rather than a single global limit, so the ring buffer is filled
        correctly regardless of how many distinct tags have reported.
        """
        try:
            with closing(sqlite3.connect(self._db_path)) as conn, conn:
                macs = [row[0] for row in conn.execute("SELECT DISTINCT mac FROM readings")]
                for mac in macs:
                    rows = conn.execute(
                        """
                        SELECT data FROM readings
                        WHERE mac = ?
                        ORDER BY timestamp DESC, id DESC
                        LIMIT ?
                        """,
                        (mac, self._max_entries),
                    ).fetchall()
                    # Rows are newest-first; reverse so deque fills oldest→newest
                    buf = deque(maxlen=self._max_entries)
                    for (data_json,) in reversed(rows):
                        buf.append(json.loads(data_json))
                    if buf:
                        self._data[mac] = buf
        except Exception:
            logger.exception("Failed to load from database")
            return

        total = sum(len(d) for d in self._data.values())
        if total:
            logger.info("Loaded %d record(s) from database for %d tag(s)", total, len(self._data))
