"""Strict RuuviTag GATT log reader using Bleak's public API.

Protocol: https://docs.ruuvi.com/communication/bluetooth-connection/
nordic-uart-service-nus/log-read

Only an explicit end marker makes a download successful. In particular,
silence, disconnection and the tag's error marker must never advance the
backfill cursor. Values on this wire protocol are full 32-bit integers,
unlike the compact fields in advertisement packets.
"""

import asyncio
import logging
import struct
from datetime import datetime, timezone

from bleak import BleakClient, BleakScanner

logger = logging.getLogger(__name__)

_RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
_TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
_END = b"\x3a\x3a\x10" + b"\xff" * 8
_DISCOVERY_TIMEOUT = 10
_CONNECT_TIMEOUT = 20
_NOTIFICATION_TIMEOUT = 10
_CONNECT_ATTEMPTS = 3


def _decode_packet(data: bytes):
    """Return a measurement, None for end, or raise on invalid/error packets."""
    if len(data) != 11:
        raise ValueError("Invalid history packet length")
    if data[2] == 0xF0:
        raise RuntimeError("Tag reported a history read error")
    if data == _END:
        return None
    if data[0] != 0x3A or data[2] != 0x10 or data[1] not in (0x30, 0x31, 0x32):
        raise ValueError("Invalid history packet header")

    timestamp = int.from_bytes(data[3:7], "big")
    value = int.from_bytes(data[7:11], "big", signed=data[1] == 0x30)
    field = {0x30: "temperature", 0x31: "humidity", 0x32: "pressure"}[data[1]]
    return {"timestamp": timestamp, field: value / 100.0}


async def _connect(address, disconnected_callback):
    """Rediscover with a fresh client on each attempt, retaining discovery
    until connected so BlueZ cannot drop an unconnected device on scan stop.
    RuuviHub's BLE lock excludes the normal advertisement scan throughout.
    """
    for attempt in range(_CONNECT_ATTEMPTS):
        client = None
        connected = False
        phase = "discovery"
        try:
            found = asyncio.get_running_loop().create_future()

            def detected(device, advertisement):
                if device.address.lower() == address.lower() and not found.done():
                    found.set_result(device)

            async with BleakScanner(detection_callback=detected):
                try:
                    device = await asyncio.wait_for(found, timeout=_DISCOVERY_TIMEOUT)
                except asyncio.TimeoutError as exc:
                    raise TimeoutError("Tag was not discovered within the discovery timeout") from exc
                phase = "connection"
                client = BleakClient(
                    device, disconnected_callback=disconnected_callback,
                    timeout=_CONNECT_TIMEOUT,
                )
                try:
                    await asyncio.wait_for(client.connect(), timeout=_CONNECT_TIMEOUT)
                except asyncio.TimeoutError as exc:
                    raise TimeoutError("GATT connection did not complete within the connection timeout") from exc
            if not client.is_connected:
                raise ConnectionError("Tag disconnected during discovery cleanup")
            connected = True
            return client
        except Exception as exc:
            logger.warning(
                "History %s attempt %d/%d failed for %s: %s",
                phase, attempt + 1, _CONNECT_ATTEMPTS, address, exc,
            )
            if attempt + 1 == _CONNECT_ATTEMPTS:
                raise
        finally:
            if client is not None and not connected:
                await client.disconnect()
        await asyncio.sleep(1)


async def download_history(address: str, start_time=None) -> list[dict]:
    """Read a complete environmental log, or raise without returning a partial log.

    The caller supplies the overall timeout. Discovery, connection and idle
    notification waits also have individual bounds. No dependency globals or
    installed third-party modules are modified.
    """
    packets = asyncio.Queue()
    active_client = None

    def disconnected(client):
        if client is active_client:
            packets.put_nowait(ConnectionError("Tag disconnected before history end marker"))

    def notified(characteristic, data):
        # Live heartbeat notifications share the NUS characteristic.
        if data and data[0] == 0x05:
            return
        packets.put_nowait(bytes(data))

    client = await _connect(address, disconnected)
    active_client = client
    try:
        await client.start_notify(_TX_UUID, notified)
        now = int(datetime.now(timezone.utc).timestamp())
        since = int(start_time.timestamp()) if start_time is not None else 0
        command = b"\x3a\x3a\x11" + struct.pack(">II", now, since)
        await client.write_gatt_char(_RX_UUID, command, response=False)
        records = []
        while True:
            try:
                packet = await asyncio.wait_for(packets.get(), timeout=_NOTIFICATION_TIMEOUT)
            except asyncio.TimeoutError as exc:
                raise TimeoutError("History stalled before end marker; download incomplete") from exc
            if isinstance(packet, Exception):
                raise packet
            record = _decode_packet(packet)
            if record is None:
                return records
            records.append(record)
    finally:
        await client.disconnect()
