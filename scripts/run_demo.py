#!/usr/bin/env python3
"""Run a local dashboard with disposable settings and synthetic sensor history."""

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import logging
import math
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from settings import SettingsStore
from config import load_config
from storage import Storage
from web.app import create_app


async def build_demo_app(directory: Path, now: datetime | None = None):
    """Build the regular web app using only files inside a fresh demo directory."""
    now = now or datetime.now(timezone.utc)
    config_path = directory / "config.json"
    config_path.write_text(json.dumps({
        "datetime_format": load_config(str(ROOT / "config.json")).datetime_format,
    }), encoding="utf-8")
    store = SettingsStore(
        settings_path=str(directory / "settings.local.json"),
        config_path=str(config_path),
    )
    tags = {
        "AA:BB:CC:DD:EE:01": "Demo Living Room",
        "AA:BB:CC:DD:EE:02": "Demo Outdoors",
        "AA:BB:CC:DD:EE:03": "Demo Garage",
    }
    db_path = str(directory / "demo.db")
    await store.update(
        db_path=db_path, scan_enabled=False, cloud_upload_enabled=False,
        history_backfill_enabled=False, history_backfill_on_startup=False,
        https_enabled=False, dashboard_refresh_seconds=5, tag_names=tags,
    )
    storage = Storage(max_entries_per_tag=1000, db_path=db_path)
    for tag_index, mac in enumerate(tags):
        # Garage is deliberately stale so both dashboard states are visible.
        latest = now - timedelta(seconds=10 if tag_index < 2 else 600)
        for index in range(289):
            wave = math.sin(index / 288 * 2 * math.pi)
            await storage.add(mac, {
                "mac": mac,
                "timestamp": (latest - timedelta(minutes=5 * (288 - index))).isoformat(),
                "data_format": 5,
                "temperature": round([21, 8, 15][tag_index] + wave * [2, 6, 3][tag_index], 2),
                "humidity": round(50 + wave * 15, 2),
                "pressure": round(1013 + wave * 4, 2),
                "battery": 2950 - tag_index * 100,
                "rssi": -55 - tag_index * 10,
            })
    return create_app(storage, store)


async def main(port: int = 8080):
    import uvicorn

    with tempfile.TemporaryDirectory(prefix="ruuvihub-demo-") as directory:
        app = await build_demo_app(Path(directory))
        logging.getLogger(__name__).info("Seeded three demo sensors; data is discarded when stopped")
        await uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="info",
        )).serve()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main(args.port))
