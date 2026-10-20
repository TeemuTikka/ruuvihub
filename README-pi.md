# RuuviHub – Raspberry Pi Deployment Guide

This guide covers deploying RuuviHub on a **Raspberry Pi** and running it as a
**systemd service** that starts automatically on boot and restarts after failures.
Installation and updates use a **release archive** built by GitHub Actions —
no git or GitHub access is required on the Pi itself, only the ability to
transfer a `.tar.gz` file to it (scp, USB, or a direct `curl` if the Pi has
internet access, which it needs anyway for `apt`/`pip`).

For Windows development, see [README-windows.md](README-windows.md). For
project structure, key classes, and debugging guidance, see
[README-dev.md](README-dev.md).

---

## Requirements

- Raspberry Pi 3, 4, or Zero W (built-in Bluetooth)
  - Older models: USB Bluetooth 4.0+ adapter required
- Raspberry Pi OS (Bullseye or newer), user `pi`
- Python 3.10+
- Internet access on the Pi (for `apt-get`/`pip install` during
  install/update — the release archive itself is transferred manually)
- RuuviTag sensor(s) in range

This guide (and `scripts/install.sh`/`update.sh`/`rollback.sh`) is fully
repeatable — run it on as many independent Pis as you like; each install
is self-contained under its own `/home/pi/ruuvihub/`.

---

## 1. Download the release archive

Releases are built automatically from git tags and published at
**https://github.com/TeemuTikka/ruuvihub/releases**. Grab the latest
`ruuvihub-vX.Y.Z.tar.gz` asset.

Archives also include the project's MIT `LICENSE`, the `SECURITY.md` policy,
`THIRD_PARTY_NOTICES.md`, and `third_party_licenses/`. Keep these with the
installed release. The public release series starts at `v1.1.0`.

**On your Windows/Mac/Linux machine**, download it from the Releases page,
then copy it to the Pi:
```powershell
scp ruuvihub-vX.Y.Z.tar.gz pi@<pi-ip>:/home/pi/
```

**Or directly on the Pi** (since it has internet access anyway):
```bash
curl -LO https://github.com/TeemuTikka/ruuvihub/releases/download/vX.Y.Z/ruuvihub-vX.Y.Z.tar.gz
```

Then extract it:
```bash
cd /home/pi
tar -xzf ruuvihub-vX.Y.Z.tar.gz
cd ruuvihub-vX.Y.Z
```

---

## 2. (Optional) System setup check

`install.sh` runs `apt-get install bluetooth bluez` for you (skip with
`--skip-apt` if you'd rather manage packages yourself). It's still worth
confirming the adapter is visible before installing:
```bash
hcitool dev
# Expected: hci0  B8:27:EB:xx:xx:xx
# If nothing shows, reboot: sudo reboot
```

---

## 3. Install

From inside the extracted archive directory:
```bash
sudo ./scripts/install.sh
```

This sets up everything under `/home/pi/ruuvihub/`:
- `releases/vX.Y.Z/` — this version's code
- `shared/` — `config.json`, `settings.local.json` (created on first run),
  `ruuvi_data.db`, `logs/` (one log file per day), `certs/` (self-signed
  HTTPS certificate, generated on first install), and per-version Python
  virtualenvs under `venvs/`. The persistent data here (settings,
  database, logs, certificate) is created once and **persists across
  updates** — it is not deleted or replaced. `settings.local.json` may
  still be rewritten *in place* by small, idempotent one-time
  migrations (e.g. persisting a TLS cert path, or moving an
  older-format log file into `logs/`) so existing installs pick up
  new defaults automatically; these never discard your own custom
  settings values. The *active* virtualenv **does** change on
  every update, since each release gets its own `shared/venvs/vX.Y.Z/`
  (see "Updating" below)
- `current` — a symlink to the active release
- `/etc/systemd/system/ruuvihub.service` — enabled and started automatically

Options:
```bash
sudo ./scripts/install.sh --base-dir /home/pi/ruuvihub --user pi   # defaults shown
sudo ./scripts/install.sh --skip-apt                               # skip apt-get bluetooth/bluez
```

If you want non-default first-run settings (scan interval, tag whitelist,
retention, etc.), edit `config.json` in the extracted archive **before**
running `install.sh` — it's only used to bootstrap `shared/settings.local.json`
the very first time. After that, use the web UI's Config page instead (see
[main README](README.md#settings-configjson-vs-settingslocaljson)).

The script waits for the service to report healthy (`systemctl is-active`
+ a local `/healthz` check) and prints the dashboard URL when done, e.g.:
```
Open https://192.168.1.42:8443 in a browser.
The certificate is self-signed -- your browser will warn once; accept/proceed to continue.
Default admin login: admin / ruuvihub — sign in to open Config and change it.
```

---

## 4. Verify BLE connection (optional but recommended)

`test_connection.py` is installed alongside the management scripts and
only needs the `ruuvitag-sensor` package, already present in the shared
virtualenv created by `install.sh`:
```bash
sudo /home/pi/ruuvihub/shared/venv/bin/python3 /home/pi/ruuvihub/scripts/test_connection.py
```

Expected output:
```
Scanning for Ruuvi tags for 10 seconds...

Found 2 tag(s):

  MAC: AA:BB:CC:DD:EE:01
    Temperature : 22.86 °C
    ...

Connection test PASSED.
```

> **Tip:** Once you have your tags' MAC addresses, set `tag_addresses` in
> `shared/config.json` (before first run) or via the Config page (after)
> to avoid picking up neighbours' tags.

---

## 5. Updating

When a new release is published, transfer the new archive to the Pi the
same way as step 1, then run:
```bash
sudo /home/pi/ruuvihub/scripts/update.sh /home/pi/ruuvihub-vX.Y.Z.tar.gz
```
(`update.sh` is installed to `/home/pi/ruuvihub/scripts` by `install.sh`
and refreshed on every successful update, so it's always available —
you don't need to re-extract the new archive first, or keep the old one
around.)

This:
1. Unpacks the new version into a fresh `releases/vX.Y.Z/`
2. Builds a fresh, release-specific virtualenv (the previous release's
   virtualenv is left untouched until the switch below succeeds, so a
   failed update can restore it exactly)
3. Stops the service, flips `current` (and the active virtualenv) to
   the new release, restarts it
4. Verifies the new version is healthy (`systemctl is-active` **and** a
   local `curl /healthz` reporting the new version)
5. **Automatically rolls back** to the previous version (code and
   virtualenv) if either check fails, and exits non-zero
6. Prunes old release directories and virtualenvs, keeping the last 3
   plus the version just rolled back from (override with `--keep N`)

`settings.local.json`, `ruuvi_data.db`, `logs/`, and `certs/` live
in `shared/` and persist across updates — they are never deleted or
replaced wholesale. `update.sh` may still rewrite `settings.local.json`
*in place* via small, idempotent one-time migrations (e.g. persisting a
TLS cert path), and the application itself migrates an older-format log
file location into `logs/` at startup; both are no-ops on an
already-migrated install and never discard your own custom settings.
Each release's Python dependencies live in their own
`shared/venvs/vX.Y.Z/` directory instead of a single mutated virtualenv,
so `shared/venv` (a symlink to the active one) changes on every update
even though the rest of `shared/` otherwise keeps its existing content.

### Rolling back manually

To roll back on demand (not just when an update's health check fails):
```bash
sudo /home/pi/ruuvihub/scripts/rollback.sh            # rolls back to the previous release
sudo /home/pi/ruuvihub/scripts/rollback.sh v1.2.0     # rolls back to a specific release
```

### Uninstalling

```bash
sudo /home/pi/ruuvihub/scripts/uninstall.sh                 # removes everything, including shared/ (settings, db, log, venvs)
sudo /home/pi/ruuvihub/scripts/uninstall.sh --keep-data     # removes the service + code, but keeps shared/ for a future reinstall
```
This stops and disables the systemd service, removes
`/etc/systemd/system/ruuvihub.service`, and (unless `--keep-data` is
passed) deletes `/home/pi/ruuvihub` entirely.

### Confirming which version is running

Every page footer shows the running version, and it's included in the
health-check endpoint:
```bash
curl -k https://localhost:8443/healthz
# {"status": "ok", "version": "v1.2.3"}
```

---

## 6. Managing the service

```bash
# View live logs
sudo journalctl -u ruuvihub.service -f

# View last 100 log lines
sudo journalctl -u ruuvihub.service -n 100

# Stop the service
sudo systemctl stop ruuvihub.service

# Restart the service (e.g. after editing shared/config.json)
sudo systemctl restart ruuvihub.service

# Disable auto-start on boot
sudo systemctl disable ruuvihub.service
```

---

## 7. Inspecting stored data (SQLite)

The database lives at `/home/pi/ruuvihub/shared/ruuvi_data.db` regardless
of which release is currently active.

```bash
# Last 10 readings
sqlite3 /home/pi/ruuvihub/shared/ruuvi_data.db \
  "SELECT mac, timestamp, data FROM readings ORDER BY id DESC LIMIT 10;"

# Pretty table with formatted sensor values
sqlite3 -column -header /home/pi/ruuvihub/shared/ruuvi_data.db "
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
sqlite3 -column -header /home/pi/ruuvihub/shared/ruuvi_data.db \
  "SELECT mac, COUNT(*) AS readings FROM readings GROUP BY mac;"

# Total reading count
sqlite3 /home/pi/ruuvihub/shared/ruuvi_data.db "SELECT COUNT(*) FROM readings;"
```

### Interactive SQLite shell

```bash
sqlite3 /home/pi/ruuvihub/shared/ruuvi_data.db
```

```
.headers on
.mode column
.tables
.schema readings
.quit
```

---

## 8. Configuring the cloud uploader

Cloud uploading is **off by default**, even once an endpoint is
configured — you turn it on explicitly. Open the web UI's Config page
(`https://<pi-ip>:8443/config`) and fill in the **Cloud Upload** section:

- **Endpoint URL** – e.g. your Azure Function / Cloud Run URL.
- **Auth header name/value** – any header your endpoint expects (e.g.
  `x-functions-key` / `<your key>`, or `Authorization` /
  `Bearer <token>`). Leave the name blank if your endpoint needs no auth
  header.
- Click **Test connection** to send one synthetic record and confirm
  the endpoint accepts it before enabling uploads.
- Tick **Enable cloud uploading** once the test succeeds.

These fields are stored in `shared/settings.local.json` and take effect
on the next upload cycle — **no service restart needed** (and they
survive every future update, since `shared/` persists in place). See
[README-dev.md](README-dev.md#srcuploaderpy--uploader) for the
`Uploader` implementation details.

---

## 9. History backfill (recovering gaps after a Pi/Bluetooth outage)

RuuviHub can optionally recover some of the readings missed while the Pi
was down, offline, or out of Bluetooth range, by pulling each tag's
on-device GATT history log instead of just relying on passive scanning.

**Requirements and limits** (see the
[developer guide](README-dev.md#srchistory_backfillpy--gatt-history-backfill)
for implementation details and protocol references):
- Requires tag firmware **≥ 3.30.0** — tags on older firmware can't be
  backfilled and are skipped.
- Only recovers **temperature/humidity/pressure** (no battery, RSSI, or
  acceleration — those aren't stored on-device).
- Can only recover gaps up to the tag's on-device log depth (~12 days at
  firmware defaults) — anything older is permanently gone.

**On by default (startup pass only).** Connecting to a tag via GATT briefly monopolizes the
BLE radio, pausing passive scanning while it runs (RuuviHub serializes
the two internally so they never run at once). Configure it from the Config
page's **History Backfill** section:

- **Enable history backfill** – master switch.
- **Run once on startup** – pulls history for every known tag once when
  the service starts (the main scenario: recovering from a Pi reboot).
- **Run periodically** – keeps re-running on the interval below (both
  can be on at once).
- **Periodic interval (seconds)** – default 3600 (1 hour).
- **Consecutive failures before auto-disabling a tag** – default 5. A
  tag that keeps failing (unreachable, out of range, old firmware) is
  automatically removed from backfill so it doesn't keep stalling every
  cycle; re-enable it with the **Re-enable** button once it's reachable
  again or its firmware is updated.

Backfilled readings are stored and displayed exactly like normal scans —
no separate view or export needed.

---

## 10. HTTPS and certificates

HTTPS is **on by default**. `install.sh` generates a self-signed
certificate the first time it runs and stores it at
`shared/certs/ruuvihub.{crt,key}` — persisted across every future update
just like `settings.local.json`, so you only click through the browser's
"not trusted" warning once per browser/device, not on every release.

- The dashboard is served at **`https://<pi-ip>:8443`**.
- The plain-HTTP port (**8080**) still works — it just 308-redirects to
  the HTTPS URL, so old bookmarks/links keep working.
- Because the certificate is self-signed (there's no public DNS name to
  get a real cert for on a home LAN), your browser will show a warning
  the first time you connect. Click "Advanced" → "Proceed" (wording
  varies by browser) — the connection is still fully encrypted, only the
  automated trust check fails.
- **Want a browser-trusted cert instead?** Generate one with
  [`mkcert`](https://github.com/FiloSottile/mkcert) (installs a local
  CA your devices already trust) or use a real cert if you have a
  domain pointed at your network, then set `tls_cert_path`/
  `tls_key_path` in `shared/settings.local.json` (these are file-only —
  not exposed on the Config page — since changing them requires a
  service restart) to point at your files and restart the service —
  RuuviHub never overwrites an existing cert/key pair.
- To disable HTTPS entirely (e.g. terminating TLS at an upstream reverse
  proxy instead), set `https_enabled` to `false` in
  `shared/settings.local.json` (also file-only) and restart.

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `hcitool dev` shows no adapter | Run `sudo reboot`, check adapter is plugged in |
| `install.sh`/`update.sh` says "must be run as root" | Prefix with `sudo` |
| Service fails to start / update rolled back automatically | Check logs: `sudo journalctl -u ruuvihub.service -n 100`. The failed release is left at `releases/<version>/` for inspection — it isn't deleted on a failed update |
| No tags found | Ensure tags are powered on and in range; check `tag_addresses` in `shared/config.json`/Config page |
| BLE permission error | Make sure the `pi` user (or whatever `--user` you passed) matches `User=` in `/etc/systemd/system/ruuvihub.service` |
| Python not found | Verify venv path: `ls /home/pi/ruuvihub/shared/venv/bin/python3` |
| Can't reach the web UI at port 8443/8080 | Confirm the service is running; check firewall rules on the Pi allow ports 8443 (HTTPS) and 8080 (HTTP redirect) on your LAN; the UI binds `0.0.0.0` so it should be reachable from any device on the same network |
| Browser warns the connection isn't private/trusted | Expected with the auto-generated self-signed certificate — the connection is still encrypted, click through the warning, or supply your own trusted cert (see "HTTPS and certificates" above) |
| Forgot the web UI password | Stop the service, delete `shared/settings.local.json`, restart — resets login to the default `admin`/`ruuvihub` (does not touch `shared/ruuvi_data.db`) |
| Need to undo an update that "succeeded" but behaves wrong | `sudo ./scripts/rollback.sh` |
| `update.sh` fails with `persist_tls_paths.py: ... Permission denied` | Your installed `scripts/update.sh` predates the fix in v1.0.6 (it ran the TLS-path helper as the service user against a root-only-readable temp dir). Manually add `chmod 755 "$EXTRACT_DIR"` right after the `EXTRACT_DIR="$(mktemp -d)"` line in `/home/pi/ruuvihub/scripts/update.sh`, then re-run the update — the fixed script is copied into place automatically on the next successful update |
| `update.sh` exits with a garbled error (e.g. `command not found`, `cp: missing file operand`) right after `Refreshing management scripts...`, but the service is actually running and `/healthz` reports the new version | Your installed `scripts/update.sh` predates a fix for a self-overwrite bug: the very last step of the update replaces every file under `scripts/` (including `update.sh` itself) while `update.sh` is still executing that same file, and an in-place `cp` can corrupt the running script mid-read. This crash happens **after** the release switch, service restart, and health check have already completed successfully — so the update itself worked; only the tail-end script/venv-pruning step was skipped. Check `/healthz` to confirm the new version, then just re-run `update.sh` once more (from the now-refreshed, fixed `scripts/update.sh`) to complete the pruning step — this only happens on the first update past this fix, since the fixed script updates itself safely (write-to-temp-then-atomic-rename) from then on |
