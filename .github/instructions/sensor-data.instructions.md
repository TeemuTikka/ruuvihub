---
description: "Use when changing RuuviTag data fetching, BLE scanning, GATT history, backfill, SQLite readings, uploads, sensor timestamps, or their tests."
applyTo: "{src/main.py,src/scanner.py,src/tag_history.py,src/history_backfill.py,src/storage.py,src/uploader.py,src/web/sensor.py,src/web/static/sensor.js,tests/test_main.py,tests/test_scanner.py,tests/test_tag_history.py,tests/test_history_backfill.py,tests/test_storage.py,tests/test_uploader.py,tests/test_sensor_page.py}"
---

# Sensor data and backfill

This covers fetching RuuviTag live advertisements and GATT history, together
with downstream storage, timestamp handling and uploads.

- When handling RuuviTag integration, always check upstream documentation
  for current usage guidelines and best practices relevant to the change:
  [ruuvitag-sensor documentation](https://ttu.github.io/ruuvitag-sensor/),
  [Ruuvi Bluetooth communication documentation](https://docs.ruuvi.com/communication),
  and, for direct BLE/GATT work,
  [Bleak troubleshooting and common mistakes](https://bleak.readthedocs.io/en/latest/troubleshooting.html).
  Check applicability to the dependency and firmware versions in use; do not
  assume the latest documentation describes an older version. If documentation
  is unavailable, state that limitation rather than claim it was checked.
- Preserve shared BLE radio serialization across scanning, scheduled
  backfill and manual backfill. Scanning skips a cycle when the lock is held;
  backfill waits. Do not block unrelated application tasks on BLE or network
  operations.
- Preserve backfill cursor and retry semantics: a failed or incomplete
  history pull must not advance the cursor or silently accept partial data.
  History timestamps and per-measurement entries need the existing merge
  and deduplication behavior; see [README-dev.md](../../README-dev.md).
- Preserve the SQLite-backed pending queue and acknowledge only the IDs
  actually delivered after a successful upload. Delivery is at least once,
  not exactly once; failed uploads remain pending across restarts.
- Test relevant ordering, duplicate/tied timestamps, partial data, failures,
  restart and concurrent operations with synthetic records or protocol
  packets. Mocked tests do not establish native BLE behavior: report
  separately whether representative hardware was tested.
- Use temporary databases and synthetic data; do not read or expose a real
  installation's readings or credentials.
