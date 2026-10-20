"""BLE scanner for Ruuvi tags using ruuvitag-sensor."""

import logging
from datetime import datetime, timezone
from typing import Dict, List

from ruuvitag_sensor.ruuvi import RuuviTagSensor

from config import Config

logger = logging.getLogger(__name__)


async def scan(config: Config) -> List[dict]:
    """Scan for Ruuvi tags and return a list of timestamped readings.

    Uses get_data_for_sensors_async which scans for scan_timeout_seconds
    and returns the latest reading per tag.

    Args:
        config: Loaded Config instance.

    Returns:
        List of dicts, each containing mac, timestamp, and sensor data.
    """
    macs = config.tag_addresses if config.tag_addresses else None
    timeout = config.scan_timeout_seconds

    logger.info("Starting BLE scan (timeout=%ds, filter=%s)", timeout, macs or "all")

    try:
        raw: Dict[str, dict] = await RuuviTagSensor.get_data_for_sensors_async(
            macs, timeout
        )
    except Exception:
        logger.exception("BLE scan failed")
        return []

    timestamp = datetime.now(timezone.utc).isoformat()
    records = []
    for mac, data in raw.items():
        record = {"mac": mac, "timestamp": timestamp, **data}
        records.append(record)
        logger.debug("Found tag %s: temp=%.2f°C", mac, data.get("temperature", 0))

    logger.info("Scan complete – %d tag(s) found", len(records))
    return records
