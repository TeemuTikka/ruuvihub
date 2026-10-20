#!/usr/bin/env python3
"""Benchmark dashboard card construction using a disposable database.

Requires the app dependencies. This is server work, not HTTP/network or
browser rendering. Each combination loads its startup ring buffers from
synthetic persisted history, without BLE or production data access.
"""

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import platform
import sqlite3
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from settings import Settings
from storage import Storage
from web.dashboard import _build_dashboard_cards


async def benchmark(tags, buffered, repetitions):
    until = datetime(2026, 10, 4, tzinfo=timezone.utc)
    results = []
    with tempfile.TemporaryDirectory(prefix="ruuvihub-dashboard-") as directory:
        for tag_count in tags:
            for buffer_size in buffered:
                db = str(Path(directory) / f"{tag_count}-{buffer_size}.db")
                Storage(db_path=db)
                names = {}

                def fixture():
                    for tag in range(tag_count):
                        mac = f"{tag:012X}"
                        names[mac] = f"Tag {tag:04}"
                        for index in range(buffer_size):
                            timestamp = (until - timedelta(minutes=buffer_size - index)).isoformat()
                            record = {"mac": mac, "timestamp": timestamp,
                                      "temperature": 20, "humidity": 50,
                                      "pressure": 1000, "battery": 2900}
                            yield mac, timestamp, json.dumps(record)

                with sqlite3.connect(db) as conn:
                    conn.executemany("INSERT INTO readings(mac, timestamp, data) VALUES (?, ?, ?)", fixture())
                storage = Storage(max_entries_per_tag=buffer_size, db_path=db)
                settings = Settings(tag_names=names)
                timings = []
                for _ in range(repetitions):
                    started = time.perf_counter()
                    cards = await _build_dashboard_cards(storage, settings)
                    timings.append((time.perf_counter() - started) * 1000)
                    if len(cards) != tag_count:
                        raise RuntimeError("Missing dashboard cards")
                results.append({"tags": tag_count, "buffered_per_tag": buffer_size,
                                "median_ms": statistics.median(timings),
                                "p95_ms": sorted(timings)[math.ceil(repetitions * .95) - 1],
                                "cards": len(cards)})
    return {"platform": platform.platform(), "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version, "repetitions": repetitions,
            "dashboard": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tags", type=int, nargs="+", default=[1, 10, 50])
    parser.add_argument("--buffered", type=int, nargs="+", default=[100, 1000])
    parser.add_argument("--repetitions", type=int, default=30)
    args = parser.parse_args()
    if min(*args.tags, *args.buffered, args.repetitions) < 1:
        parser.error("tags, buffered, and repetitions must be positive")
    print(json.dumps(asyncio.run(benchmark(args.tags, args.buffered, args.repetitions)), indent=2))
