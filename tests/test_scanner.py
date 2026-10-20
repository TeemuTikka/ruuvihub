"""Tests for src/scanner.py using mocked ruuvitag-sensor."""

import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from config import Config
from mock_ruuvi import MAC_1, MAC_2, make_raw_scan_result


@pytest.mark.asyncio
async def test_scan_returns_records_for_all_tags():
    raw = make_raw_scan_result([MAC_1, MAC_2])
    with patch(
        "scanner.RuuviTagSensor.get_data_for_sensors_async",
        new=AsyncMock(return_value=raw),
    ):
        from scanner import scan
        config = Config()
        records = await scan(config)

    assert len(records) == 2
    macs = {r["mac"] for r in records}
    assert macs == {MAC_1, MAC_2}


@pytest.mark.asyncio
async def test_scan_record_has_timestamp():
    raw = make_raw_scan_result([MAC_1])
    with patch(
        "scanner.RuuviTagSensor.get_data_for_sensors_async",
        new=AsyncMock(return_value=raw),
    ):
        from scanner import scan
        config = Config()
        records = await scan(config)

    assert "timestamp" in records[0]


@pytest.mark.asyncio
async def test_scan_applies_whitelist():
    """Scanner passes tag_addresses to the library."""
    raw = make_raw_scan_result([MAC_1])
    mock_fn = AsyncMock(return_value=raw)
    with patch("scanner.RuuviTagSensor.get_data_for_sensors_async", new=mock_fn):
        from scanner import scan
        config = Config(tag_addresses=[MAC_1])
        await scan(config)

    mock_fn.assert_called_once_with([MAC_1], config.scan_timeout_seconds)


@pytest.mark.asyncio
async def test_scan_passes_none_when_no_whitelist():
    """Scanner passes None (scan all) when tag_addresses is empty."""
    raw = make_raw_scan_result([MAC_1])
    mock_fn = AsyncMock(return_value=raw)
    with patch("scanner.RuuviTagSensor.get_data_for_sensors_async", new=mock_fn):
        from scanner import scan
        config = Config(tag_addresses=[])
        await scan(config)

    mock_fn.assert_called_once_with(None, config.scan_timeout_seconds)


@pytest.mark.asyncio
async def test_scan_returns_empty_on_ble_error():
    with patch(
        "scanner.RuuviTagSensor.get_data_for_sensors_async",
        new=AsyncMock(side_effect=Exception("BLE error")),
    ):
        from scanner import scan
        config = Config()
        records = await scan(config)

    assert records == []
