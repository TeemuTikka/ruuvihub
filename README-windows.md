# RuuviHub – Windows Guide

Install and update the packaged app using the steps below. Development from
source is covered [later in this guide](#development-from-source).
For Raspberry Pi, see [README-pi.md](README-pi.md).

## Install (no development setup needed)

You need **64-bit Windows 11**, internet access for setup, a Bluetooth LE
adapter with Bluetooth turned on, and RuuviTags in range. Python is included
in the installer; you do not need to install it separately.

1. Open the [latest release](https://github.com/TeemuTikka/ruuvihub/releases/latest)
   and download **`ruuvihub-vX.Y.Z-windows-x64.exe`** from **Assets**.
2. Run it while signed in to the account that will use RuuviHub.
   Setup checks the release signature and installer digest. The installer
   is not Authenticode-signed, so Windows may warn that the publisher is
   unknown. Use the download from this project's release page.
3. Open **https://localhost:8443** and accept the self-signed certificate
   warning for your local installation.
4. Sign in with **`admin` / `ruuvihub`**, then change the password under
   **Settings → System settings**. Nearby tags appear after a scan cycle.

The managed install runs under your logged-in user, so scanning pauses
when you sign out.

Tray-enabled releases run in the notification area instead of keeping a
console window open. Look for **RuuviHub** under the taskbar's hidden icons
if it is not shown directly. Click the icon or choose **Open dashboard**
from its menu. Logs still go to `%LOCALAPPDATA%\RuuviHub\logs` by default.
The original `v2.0.0` installer still opens a console. `v2.0.1` detaches
after launch and may briefly flash a window. Later bundles start the
scheduled app without creating a console; the updater still prints to
the terminal you run it from.

## Update, revert, and uninstall

The app and its Python runtime install under
`%LOCALAPPDATA%\Programs\RuuviHub`; data lives under `%LOCALAPPDATA%\RuuviHub`.
Open PowerShell under the same Windows account. Check the installed/latest
version with `status`, then install the latest release with `update`:

```powershell
& "$env:LOCALAPPDATA\Programs\RuuviHub\ruuvihub-update.cmd" status
& "$env:LOCALAPPDATA\Programs\RuuviHub\ruuvihub-update.cmd" update
```

`update` backs up settings and SQLite before switching releases. If you need
to return to the previous version, run:

```powershell
& "$env:LOCALAPPDATA\Programs\RuuviHub\ruuvihub-update.cmd" revert
```

`revert` works offline and keeps the current data. To uninstall, use **Settings >
Apps > Installed apps**; remove your data directory separately only if
you no longer need its contents.

### Windows 11 validation

On Windows 11 with an ASUS USB-BT500, a live scan detected nearby RuuviTags.
The bundled app also ran under its per-user scheduled task with scanning
enabled and stored sensor readings. A disposable installation exercised
task start, update, revert, failed-health recovery, and uninstall while
confirming that its data remained in place. A separate Windows 11 check
used the published `v2.0.0` installer with disposable data and BLE disabled:
setup verified the release and started a healthy scheduled task, `status`
and `update` confirmed it was current, and uninstall removed the task and
app files while preserving settings and SQLite. Sign-out/sign-in and
update/revert between two published signed releases remain untested.

---

## Development from source

The remaining setup and test steps are for changing or running the source
code. For the shared first-change workflow and VS Code setup, see
[README-dev.md](README-dev.md#start-here-first-code-change).

### Development requirements

- 64-bit Windows 11
- Python 3.11+ ([python.org](https://www.python.org/downloads/))
- Bluetooth LE support: a built-in adapter, or a USB Bluetooth 4.0+
  adapter (e.g. ASUS USB-BT500), with Bluetooth turned on in Windows
  Settings
  - `ruuvitag-sensor` uses the `bleak` library on Windows (WinRT APIs),
    **not** the Linux-only `bluepy` — real BLE scanning works natively
    on Windows, no Docker/WSL/Linux VM needed
  - Unit tests work fine **without** any Bluetooth adapter

---

## 1. One-time PowerShell setup

By default, Windows blocks running `.ps1` scripts (including venv activation). Fix this once:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

---

## 2. Install development dependencies

```powershell
git clone https://github.com/TeemuTikka/ruuvihub.git
cd ruuvihub

python -m venv .venv
.venv\Scripts\activate

pip install -r requirements.txt
```

This pulls in `ruuvitag-sensor`, which on Windows automatically installs
`bleak` plus the WinRT Bluetooth packages it needs — no extra Bluetooth
stack setup required beyond having an adapter and Bluetooth enabled.

---

## 3. Configure

`config.json` is only used to **bootstrap** settings the very first time
you run the app — after that, a git-ignored `settings.local.json` is
created automatically and becomes the live source of truth. Once that
file exists, use the web UI's Config page (`/config`, see step 4) to
change settings instead of editing either JSON file by hand. See the
[main README](README.md#settings-configjson-vs-settingslocaljson) for
details on this split.

Keep the project folder restricted to your Windows account: settings hold
cloud credentials and the session-signing secret, and generated files
inherit the parent folder's Windows permissions. POSIX `0600` file modes
do not configure Windows ACLs.

Edit `config.json` (before first run) if you want different defaults:

```json
{
  "scan_enabled": true,
  "scan_interval_seconds": 60,
  "scan_timeout_seconds": 5,
  "upload_interval_seconds": 300,
  "max_entries_per_tag": 100,
  "tag_addresses": [],
  "db_path": ""
}
```

| Field | Default | Description |
|---|---|---|
| `scan_enabled` | `true` | Enable/disable BLE scanning without restarting the app |
| `scan_interval_seconds` | 60 | How often to trigger a BLE scan |
| `scan_timeout_seconds` | 5 | How long each BLE scan window lasts |
| `upload_interval_seconds` | 300 | How often to run the upload job |
| `max_entries_per_tag` | 100 | Max readings kept per tag in memory |
| `tag_addresses` | `[]` | Whitelist of MAC addresses; empty = scan all |
| `db_path` | `""` | SQLite file path; empty = `ruuvi_data.db` next to `main.py` |
| `db_retention_days` | 0 | Delete DB readings older than N days; 0 = keep forever |
| `https_enabled` | `true` | Serve the web UI over HTTPS (auto-generated self-signed cert); see "Run the application" below for the debug-only HTTP override |

---

## 4. Run the application

```powershell
# With venv activated:
python src\main.py
```

Then open **https://localhost:8443** in a browser for the public,
read-only dashboard and per-sensor history. Sign in with the default
admin credentials (`admin`/`ruuvihub`) to open the Config page — see the
[main README](README.md#the-web-dashboard) for details.
A self-signed certificate is generated automatically on first run, so
your browser will show a one-time "not trusted" warning to click
through. `http://localhost:8080` still works too; it just redirects to
the HTTPS URL.

**Debugging via VS Code (F5)** uses the launch configs in
`.vscode/launch.json`. Select **RuuviHub: Demo dashboard (F5)** for three
dummy sensors and 24 hours of seeded history in a disposable database,
or **RuuviHub: Real sensors + Browser** to run the real scanner with your
regular settings. Both open `http://localhost:8080` with plain HTTP.
The pre-launch task uses `python` to create `.venv` if needed and install
runtime dependencies; both launches use that environment.
For test discovery, select `.venv` as described in
[README-dev.md](README-dev.md#vs-code). The real-sensor launch sets
`RUUVIHUB_DISABLE_HTTPS=true` to avoid certificate warnings. Running
`python src\main.py` directly from a terminal (as above) does **not**
set that variable, so it uses HTTPS like a normal install; set it
yourself if you want plain HTTP from a terminal too:
```powershell
$env:RUUVIHUB_DISABLE_HTTPS = "true"; python src\main.py
```

Real BLE scanning works out of the box on Windows (no Docker/WSL
needed) as long as Bluetooth is turned on and an adapter is present —
tags found nearby will start appearing on the dashboard within a scan
cycle. If no tags are found, see the troubleshooting note at the end of
this section.

Override config path:
```powershell
$env:CONFIG_PATH = "C:\path\to\my-config.json"; python src\main.py
```

Example output:
```
2026-04-17 10:00:00 [INFO] __main__: RuuviHub starting – scan every 60s, upload every 300s
2026-04-17 10:00:00 [INFO] scanner: Starting BLE scan (timeout=5s, filter=all)
2026-04-17 10:00:05 [INFO] scanner: Scan complete – 2 tag(s) found
2026-04-17 10:00:05 [INFO] __main__: Storage now holds readings from 2 unique tag(s)
2026-04-17 10:05:00 [INFO] uploader: UPLOAD STUB – would POST 2 record(s): ...
```

Press `Ctrl+C` to stop.

**No tags showing up?**
- Confirm Bluetooth is turned on: Settings → Bluetooth & devices.
- Confirm the adapter is recognized: Device Manager → Bluetooth.
- Run `python test_connection.py` (step 5 below) first to isolate
  whether the issue is BLE discovery itself vs. the rest of the app.
- If `tag_addresses` is non-empty in your settings, double-check the
  MAC address format/casing matches what your tags actually broadcast.
- Windows may prompt for a location/Bluetooth permission the first
  time an app scans — allow it if asked.

---

## 5. Tests

### Agent validation on Windows 11

Run from the repository root in PowerShell. Use the existing root virtual
environment if available; invoke its interpreter directly so activation and
the machine's default `python` don't affect the run:

```powershell
& .\.venv\Scripts\python.exe --version
& .\.venv\Scripts\python.exe -m pytest tests\ -q --ignore=tests\test_connection.py
```

For a new environment, create it with x64 Python 3.11+ (`python -m venv .venv`)
and install the locked test dependencies with
`& .\.venv\Scripts\python.exe -m pip install --require-hashes -r tests\requirements.txt`.
Run focused tests first for localized changes. For installer and updater
changes, start with `tests\test_windows_*.py` (pass the relevant filenames
explicitly to pytest) and see [Windows release payload](README-dev.md#windows-release-payload).
Use the Inno Setup version and verified downloads from the release workflow;
the build Python must match the embeddable ZIP's major/minor version.
An unpublished installer cannot pass the production GitHub release
verification step. Use a disposable test key for local signing tests,
not the production signing key, and do not push a release tag for a local build.

- Use native Windows, not Docker, for WinRT BLE, Inno Setup, and Task
  Scheduler checks. Run the excluded live BLE test only when requested and
  hardware is available; report counts or outcomes, not tag addresses.
- Test installer and scheduler lifecycles in a disposable Windows account
  or VM. Temporary directories alone do not isolate the scheduled task:
  RuuviHub uses one task per Windows user. Use disposable settings and
  SQLite data, confirm the expected version on loopback `/healthz`, and
  check update, offline revert, failure recovery, and uninstall data
  preservation separately from packaging. Do not sign out the working
  session to test logon behavior without approval.
- For Linux behavior on this machine, use
  [Docker Desktop's Linux engine](README-dev.md#testing-on-linux-via-docker-optional).
  Report the platform, Python version, test counts, and any unverified
  hardware or lifecycle steps separately.

### Separate test environment (optional)

The `tests/` directory is a separate project with its own venv.

```powershell
cd tests

python -m venv .venv
.venv\Scripts\activate

pip install -r requirements.txt
```

### Run unit tests (no Bluetooth needed)

```powershell
pytest -q --ignore=test_connection.py
```

Expected: all tests pass (93+ at the time of writing), including
`test_scanner.py` — `ruuvitag-sensor` installs and imports fine on
Windows (via `bleak`), so the scanner's mocked-library tests run here
too. Only `test_connection.py` needs real hardware (see below).

### Run live BLE connection test

Requires a USB Bluetooth adapter and at least one RuuviTag in range.

```powershell
python test_connection.py
```

Expected output:
```
Scanning for Ruuvi tags for 10 seconds...

Found 2 tag(s):

  MAC: AA:BB:CC:DD:EE:01
    Temperature : 22.86 °C
    Humidity    : 34.21 %
    Pressure    : 1011.6 hPa
    Battery     : 2999 mV
    RSSI        : -66 dBm
```

### Test files

| File | Description |
|---|---|
| `mock_ruuvi.py` | Helper – generates fake tag data matching real format |
| `test_config.py` | Config loading and validation |
| `test_settings.py` | `settings.local.json` load/save/bootstrap/password hashing |
| `test_storage.py` | In-memory + SQLite storage behaviour |
| `test_scanner.py` | Scanner with mocked BLE library (runs fine on Windows) |
| `test_uploader.py` | Uploader stub + real HTTP POST behaviour |
| `test_main.py` | Task wiring, loop crash isolation, live settings reload |
| `test_web_app.py`, `test_auth.py`, `test_frontend_assets.py`, `test_dashboard.py`, `test_sensor_page.py`, `test_config_page.py` | Web UI: app skeleton, login/session, static assets, dashboard, sensor history, config page |
| `test_connection.py` | **Live test** – requires BT adapter + tag in range |

See [README-dev.md](README-dev.md) for what each source module/class
under test does and how the fake-BLE test shim works.

---

## 6. Inspecting stored data (SQLite)

Use **System settings → Sync history now** to recover up to 10 days
of history still stored on reachable RuuviTags. This fills older gaps
without re-importing matching readings. See the
[history backfill limits](README-pi.md#9-history-backfill-recovering-gaps-after-a-pibluetooth-outage).

### Install SQLite CLI (one-time)

```powershell
winget install SQLite.SQLite
```

Open a **new PowerShell window** after install. Verify:
```powershell
sqlite3 --version
```

**GUI option:** [DB Browser for SQLite](https://sqlitebrowser.org/) – free, visual table browser.

### Useful queries

Run from the `ruuviHub` folder:

```powershell
# Last 10 raw readings
sqlite3 ruuvi_data.db "SELECT mac, timestamp, data FROM readings ORDER BY id DESC LIMIT 10;"

# Pretty table with formatted values
sqlite3 -column -header ruuvi_data.db "
  SELECT
    mac,
    timestamp,
    printf('%.2f', json_extract(data, '$.temperature')) AS temp_c,
    printf('%.2f', json_extract(data, '$.humidity'))    AS humidity,
    printf('%.2f', json_extract(data, '$.pressure'))    AS pressure_hpa,
    json_extract(data, '$.battery')                     AS battery_mv
  FROM readings
  ORDER BY id DESC
  LIMIT 20;
"

# Count readings per tag
sqlite3 -column -header ruuvi_data.db "
  SELECT mac, COUNT(*) AS readings FROM readings GROUP BY mac;
"

# Readings for a specific tag
sqlite3 -column -header ruuvi_data.db "
  SELECT timestamp,
    printf('%.2f', json_extract(data, '$.temperature')) AS temp_c
  FROM readings
  WHERE mac = 'AA:BB:CC:DD:EE:01'
  ORDER BY id DESC LIMIT 50;
"
```

### Interactive SQLite shell

```powershell
sqlite3 ruuvi_data.db
```

Useful commands inside the shell:
```
.headers on       -- show column names
.mode column      -- align columns
.tables           -- list all tables
.schema readings  -- show table definition
.quit             -- exit
```

---

## 7. Configuring the cloud uploader

Cloud uploading is **off by default**, even once an endpoint is
configured — you turn it on explicitly. Open the web UI's Config page
(`https://localhost:8443/config`) and fill in the **Cloud Upload**
section:

- **Endpoint URL** – e.g. your Azure Function / Cloud Run URL.
- **Auth header name/value** – any header your endpoint expects (e.g.
  `x-functions-key` / `<your key>`, or `Authorization` /
  `Bearer <token>`). Leave the name blank if your endpoint needs no auth
  header.
- Click **Test connection** to send one synthetic record and confirm
  the endpoint accepts it before enabling uploads.
- Tick **Enable cloud uploading** once the test succeeds.

These fields are stored in `settings.local.json`, take effect on the
next upload cycle with no restart, and can equally be edited directly
in that file if you prefer not to use the UI. See
[README-dev.md](README-dev.md#srcuploaderpy--uploader) for the
`Uploader` implementation details.
