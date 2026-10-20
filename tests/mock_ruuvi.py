"""Mock Ruuvi tag data generator for tests.

Generates data dicts that match the format returned by
ruuvitag-sensor's get_data_for_sensors_async().
"""

from datetime import datetime, timezone
from typing import Dict

# Example MAC addresses for testing
MAC_1 = "AA:BB:CC:DD:EE:01"
MAC_2 = "AA:BB:CC:DD:EE:02"


def make_sensor_data(
    temperature: float = 22.5,
    humidity: float = 45.0,
    pressure: float = 1013.25,
    battery: int = 2950,
    rssi: int = -70,
    data_format: int = 5,
) -> dict:
    """Return a sensor data payload as returned by ruuvitag-sensor."""
    return {
        "data_format": data_format,
        "humidity": humidity,
        "temperature": temperature,
        "pressure": pressure,
        "acceleration": 1000.0,
        "acceleration_x": 0,
        "acceleration_y": 0,
        "acceleration_z": 1000,
        "tx_power": 4,
        "battery": battery,
        "movement_counter": 0,
        "measurement_sequence_number": 1,
        "rssi": rssi,
    }


def make_record(
    mac: str = MAC_1,
    timestamp: str | None = None,
    **kwargs,
) -> dict:
    """Return a full storage record (mac + timestamp + sensor data)."""
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat()
    return {"mac": mac, "timestamp": timestamp, **make_sensor_data(**kwargs)}


def make_raw_scan_result(macs: list | None = None) -> Dict[str, dict]:
    """Return a dict mimicking get_data_for_sensors_async() output.

    Args:
        macs: List of MAC addresses to include. Defaults to [MAC_1, MAC_2].
    """
    if macs is None:
        macs = [MAC_1, MAC_2]
    return {mac: make_sensor_data() for mac in macs}
