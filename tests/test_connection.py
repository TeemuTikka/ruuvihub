"""Live BLE connection test – run on Raspberry Pi only.

This is a manual integration test. It requires:
  - A Raspberry Pi with Bluetooth enabled
  - At least one RuuviTag in range
  - ruuvitag-sensor installed in the venv

Run with:
    python test_connection.py

It will scan for 10 seconds and print any found tags.
"""

import asyncio
import sys

from ruuvitag_sensor.ruuvi import RuuviTagSensor


async def run_connection_test(timeout_seconds: int = 10) -> None:
    print(f"Scanning for Ruuvi tags for {timeout_seconds} seconds...")
    try:
        data = await RuuviTagSensor.get_data_for_sensors_async(None, timeout_seconds)
    except Exception as exc:
        print(f"ERROR: BLE scan failed – {exc}")
        sys.exit(1)

    if not data:
        print("No Ruuvi tags found. Check that tags are powered on and in range.")
        sys.exit(1)

    print(f"\nFound {len(data)} tag(s):\n")
    for mac, payload in data.items():
        print(f"  MAC: {mac}")
        print(f"    Temperature : {payload.get('temperature')} °C")
        print(f"    Humidity    : {payload.get('humidity')} %")
        print(f"    Pressure    : {payload.get('pressure')} hPa")
        print(f"    Battery     : {payload.get('battery')} mV")
        print(f"    RSSI        : {payload.get('rssi')} dBm")
        print()

    print("Connection test PASSED.")


if __name__ == "__main__":
    asyncio.run(run_connection_test())
