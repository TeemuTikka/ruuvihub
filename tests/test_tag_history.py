"""Exercise the BLE history transport with synthetic protocol packets."""

import asyncio
import os
import struct
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import tag_history as history

ADDRESS = "AA:BB:CC:DD:EE:01"
TS = 1_700_000_000
END = b"\x3a\x3a\x10" + b"\xff" * 8


def packet(field, value):
    return bytes([0x3A, field, 0x10]) + struct.pack(">I", TS) + value.to_bytes(
        4, "big", signed=field == 0x30,
    )


@pytest.mark.parametrize("field, value, name, expected", [
    (0x30, -1876, "temperature", -18.76),
    (0x30, 2445, "temperature", 24.45),
    (0x31, 4567, "humidity", 45.67),
    (0x32, 101325, "pressure", 1013.25),
])
def test_decodes_full_width_protocol_values(field, value, name, expected):
    assert history._decode_packet(packet(field, value)) == {
        "timestamp": TS, name: expected,
    }


@pytest.mark.parametrize("data", [
    b"\x3a\x3a\x10",  # Truncated end marker must not count as completion.
    b"\x3a\x3a\x10" + b"\x00" * 8,
    b"\x3a\x30\x99" + b"\x00" * 8,
    b"\x3a\x99\x10" + b"\x00" * 8,
])
def test_rejects_invalid_packets(data):
    with pytest.raises(ValueError):
        history._decode_packet(data)


def test_rejects_device_error():
    with pytest.raises(RuntimeError, match="history read error"):
        history._decode_packet(b"\x30\x30\xf0" + b"\xff" * 8)


@pytest.fixture
def transport(monkeypatch):
    state = SimpleNamespace(packets=[], clients=[], events=[], failures=0, discover=True,
                            disconnect_after_packets=False, drop_on_scan_stop=False)
    device = SimpleNamespace(address=ADDRESS)

    class Scanner:
        def __init__(self, detection_callback):
            self.detected = detection_callback

        async def __aenter__(self):
            state.events.append("scan start")
            self.detected(SimpleNamespace(address="AA:BB:CC:DD:EE:02"), None)
            if state.discover:
                self.detected(device, None)
            return self

        async def __aexit__(self, *args):
            state.events.append("scan stop")
            if state.drop_on_scan_stop and state.clients:
                state.clients[-1].is_connected = False

    class Client:
        def __init__(self, target, disconnected_callback, timeout):
            assert target is device  # Must use discovered device, not bare address.
            self.callback = disconnected_callback
            self.is_connected = False
            self.start_notify = AsyncMock(side_effect=self.notify)
            self.write_gatt_char = AsyncMock(side_effect=self.write)
            state.clients.append(self)

        async def connect(self):
            state.events.append("connect")
            if state.failures:
                state.failures -= 1
                raise ConnectionError("stale BlueZ device")
            self.is_connected = True

        async def disconnect(self):
            state.events.append("disconnect")
            self.is_connected = False
            self.callback(self)

        async def notify(self, characteristic, callback):
            self.notification = callback

        async def write(self, *args, **kwargs):
            for data in state.packets:
                self.notification(None, data)
            if state.disconnect_after_packets:
                self.callback(self)

    monkeypatch.setattr(history, "BleakScanner", Scanner)
    monkeypatch.setattr(history, "BleakClient", Client)
    monkeypatch.setattr(history, "_DISCOVERY_TIMEOUT", 0.01)
    monkeypatch.setattr(history, "_NOTIFICATION_TIMEOUT", 0.01)
    return state


@pytest.mark.asyncio
async def test_complete_transfer_and_command(transport):
    transport.packets = [b"\x05heartbeat", packet(0x30, -1876), packet(0x32, 101325), END]
    since = datetime.fromtimestamp(TS - 3600, tz=timezone.utc)
    rows = await history.download_history(ADDRESS.lower(), since)
    assert rows == [{"timestamp": TS, "temperature": -18.76},
                    {"timestamp": TS, "pressure": 1013.25}]
    assert transport.events == ["scan start", "connect", "scan stop", "disconnect"]
    client = transport.clients[0]
    client.start_notify.assert_awaited_once()
    args, kwargs = client.write_gatt_char.call_args
    assert args[0] == history._RX_UUID
    assert args[1][:3] == b"\x3a\x3a\x11"
    now, start = struct.unpack(">II", args[1][3:])
    assert start == TS - 3600
    assert abs(now - datetime.now(timezone.utc).timestamp()) < 10
    assert kwargs == {"response": False}


@pytest.mark.asyncio
async def test_explicit_empty_log_is_success(transport):
    transport.packets = [END]
    assert await history.download_history(ADDRESS) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("packets, disconnect, error", [
    ([], False, TimeoutError),
    ([packet(0x30, 100)], False, TimeoutError),
    ([packet(0x30, 100)], True, ConnectionError),
    ([packet(0x30, 100), b"\x30\x30\xf0" + b"\xff" * 8], False, RuntimeError),
    ([packet(0x30, 100), END[:3]], False, ValueError),
])
async def test_never_returns_partial_history(transport, packets, disconnect, error):
    transport.packets = packets
    transport.disconnect_after_packets = disconnect
    with pytest.raises(error):
        await history.download_history(ADDRESS)
    assert transport.events[-1] == "disconnect"


@pytest.mark.asyncio
async def test_retry_uses_fresh_discovery_and_client(transport):
    transport.failures = 1
    transport.packets = [END]
    assert await history.download_history(ADDRESS) == []
    assert len(transport.clients) == 2
    assert transport.clients[0] is not transport.clients[1]
    assert transport.events.count("scan start") == 2
    assert transport.events.count("disconnect") == 2


@pytest.mark.asyncio
async def test_discovery_failure_is_bounded(transport, monkeypatch):
    transport.discover = False
    monkeypatch.setattr(history, "_CONNECT_ATTEMPTS", 1)
    with pytest.raises(TimeoutError):
        await history.download_history(ADDRESS)
    assert transport.clients == []
    assert transport.events == ["scan start", "scan stop"]


@pytest.mark.asyncio
async def test_scan_stop_disconnect_is_not_success(transport, monkeypatch):
    transport.drop_on_scan_stop = True
    monkeypatch.setattr(history, "_CONNECT_ATTEMPTS", 1)
    with pytest.raises(ConnectionError, match="discovery cleanup"):
        await history.download_history(ADDRESS)
    assert transport.events[-1] == "disconnect"


@pytest.mark.asyncio
async def test_cancellation_disconnects_and_stops_scanning(transport):
    transport.packets = []
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(history.download_history(ADDRESS), timeout=0.005)
    assert transport.events[-1] == "disconnect"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["stall", "disconnect", "device error"])
async def test_failed_transfer_keeps_persisted_cursor_then_retries(transport, tmp_path, failure):
    from history_backfill import run_backfill_for_known_tags, _consecutive_failures
    from storage import Storage

    _consecutive_failures.clear()
    storage = Storage(db_path=str(tmp_path / "history.db"))
    since = datetime.fromtimestamp(TS - 3600, tz=timezone.utc).isoformat()
    await storage.set_backfill_cursor(ADDRESS, since)
    settings = SimpleNamespace(
        tag_addresses=[ADDRESS], history_backfill_disabled_tags={},
        history_backfill_failure_counts={}, history_backfill_failure_threshold=5,
        history_backfill_timeout_seconds=5,
    )
    store = SimpleNamespace(set_backfill_failure_count=AsyncMock(), set_backfill_disabled=AsyncMock())
    transport.packets = [packet(0x30, -1876)]
    transport.disconnect_after_packets = failure == "disconnect"
    if failure == "device error":
        transport.packets.append(b"\x30\x30\xf0" + b"\xff" * 8)

    await run_backfill_for_known_tags(settings, storage, store)
    assert await storage.get_backfill_cursor(ADDRESS) == since
    assert await storage.get_history(ADDRESS) == []
    store.set_backfill_failure_count.assert_awaited_once_with(ADDRESS, 1)

    transport.packets = [packet(0x30, -1876), END]
    transport.disconnect_after_packets = False
    await run_backfill_for_known_tags(settings, storage, store)
    rows = await storage.get_history(ADDRESS)
    assert len(rows) == 1
    assert rows[0]["temperature"] == -18.76
    assert await storage.get_backfill_cursor(ADDRESS) > since
    assert ADDRESS not in _consecutive_failures
