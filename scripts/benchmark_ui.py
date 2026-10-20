#!/usr/bin/env python3
"""Repeatable storage/asset baseline; no BLE, network, or production data.

Run each row count in a fresh process to compare peak RSS. This measures
storage calls, not HTTP latency or browser rendering. RSS includes fixture
creation and startup and is a process lifetime high-water mark.
"""

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import gzip
import json
import math
from pathlib import Path
import platform
import resource
import sqlite3
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from storage import Storage


async def benchmark(rows, repetitions):
    mac = "AA:BB:CC:DD:EE:FF"
    until = datetime(2026, 10, 4, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory(prefix="ruuvihub-benchmark-") as directory:
        db = str(Path(directory) / "history.db")
        storage = Storage(db_path=db)

        def fixture():
            for index in range(rows):
                timestamp = (until - timedelta(minutes=rows - index)).isoformat()
                record = {"mac": mac, "timestamp": timestamp, "data_format": 5,
                          "temperature": 20 + index % 10 / 10,
                          "humidity": None if index % 97 == 0 else 50,
                          "pressure": 1000, "battery": 2900}
                yield mac, timestamp, json.dumps(record)

        with sqlite3.connect(db) as conn:
            conn.executemany("INSERT INTO readings(mac, timestamp, data) VALUES (?, ?, ?)", fixture())
        # Load the same bounded startup cache as the deployed app.
        storage = Storage(db_path=db)
        results = []
        for label, hours in [("1h", 1), ("24h", 24), ("7d", 168), ("30d", 720), ("all", None)]:
            since = (until - timedelta(hours=hours)).isoformat() if hours else None
            timings = []
            for _ in range(repetitions):
                started = time.perf_counter()
                records = await storage.get_chart_history(mac, since, until.isoformat(), 130)
                timings.append((time.perf_counter() - started) * 1000)
                if len(records) > 500:
                    raise RuntimeError("Chart response exceeded 500 records")
            ordered = sorted(timings)
            results.append({"range": label, "first_query_ms": timings[0],
                            "median_ms": statistics.median(timings),
                            "p95_ms": ordered[math.ceil(len(ordered) * .95) - 1],
                            "records": len(records),
                            "records_json_bytes": len(json.dumps(records).encode())})
        assets = []
        for path in sorted((ROOT / "src/web/static").iterdir()):
            if path.suffix in {".js", ".css"}:
                data = path.read_bytes()
                assets.append({"asset": path.name, "raw_bytes": len(data),
                               "gzip_bytes": len(gzip.compress(data, compresslevel=6, mtime=0))})
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return {"platform": platform.platform(), "python": platform.python_version(),
                "sqlite": sqlite3.sqlite_version, "rows": rows,
                "cadence_seconds": 60, "until": until.isoformat(),
                "repetitions": repetitions, "peak_process_rss_bytes": rss if sys.platform == "darwin" else rss * 1024,
                "rss_method": "getrusage lifetime peak, including fixture creation; not isolated query RSS",
                "cache_method": "first and repeated queries; OS disk cache not cleared",
                "asset_gzip_level": 6, "history": results, "assets": assets}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=100_000)
    parser.add_argument("--repetitions", type=int, default=30)
    args = parser.parse_args()
    if args.rows < 1 or args.repetitions < 1:
        parser.error("rows and repetitions must be positive")
    print(json.dumps(asyncio.run(benchmark(args.rows, args.repetitions)), indent=2))
