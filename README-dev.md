# RuuviHub – Developer Guide

This guide covers how the codebase is organized, what each module/class
is responsible for, the runtime architecture, and how to develop/debug
RuuviHub locally — including with no real BLE hardware at all.

For installing/running the app, see [README.md](README.md),
[README-windows.md](README-windows.md), or [README-pi.md](README-pi.md).

---

## Start here: first code change

This path is for a first contribution, whether you write the code yourself
or work with an AI coding agent. Start in the repository root, the folder
that contains `src/`, `tests/`, and `requirements.txt`.

1. Read the task and agree on what a finished change should do. For a
   GitHub issue, use its description and acceptance criteria as the scope.
2. Create and activate a virtual environment, then install both runtime
   and test dependencies:

   ```bash
   python -m venv .venv
   # Windows PowerShell: .venv\Scripts\Activate.ps1
   # macOS/Linux:        source .venv/bin/activate
   python -m pip install -r requirements.txt
   python -m pip install -r tests/requirements.txt
   ```

3. Make a small change and add or update tests for the behavior. Most tests
   use mocks and need no RuuviTag or Bluetooth adapter.
4. Run the hardware-free test suite from the repository root:

   ```bash
   python -m pytest tests/ -q --ignore=tests/test_connection.py
   ```

5. Review `git diff`, fix any failures, and summarize the change and test
   result. Run `python -m pytest tests/test_connection.py -q` only when a
   Bluetooth adapter and a RuuviTag are available; that test uses real
   hardware.

For Windows-specific setup and troubleshooting, see
[README-windows.md](README-windows.md). For how the application is put
together, continue with the architecture and module reference below.

## Using AI coding agents

Use the same contribution process with any assistant: provide the issue or
task, ask it to inspect the relevant code and tests, then review its diff
and run the checks above. Keep secrets, `settings.local.json`, database
files, and private sensor data out of prompts and generated commits.

| Tool | Repository setup |
|---|---|
| GitHub Copilot | Open this repository in VS Code, enable Copilot, and use Copilot Chat. Repository guidance is in [`.github/copilot-instructions.md`](.github/copilot-instructions.md). |
| OpenAI Codex CLI | Install and sign in to Codex CLI, open a terminal at the repository root, then run `codex`. Codex CLI automatically loads this repository's [`AGENTS.md`](AGENTS.md); see the [official AGENTS.md guidance](https://developers.openai.com/api/docs/guides/latest-model#using-agents-md). |
| Other coding agents | Open the repository root as the workspace and provide this guide, the relevant issue, and the test command. The project has no provider-specific setup requirement for other agents. |

For example, give your agent a bounded task and a way to check its work:

```text
Implement <GitHub issue URL> in this repository. Read AGENTS.md and
README-dev.md, inspect the relevant code and tests, make the smallest
complete change, run the documented hardware-free tests, and summarize
the result and any remaining uncertainty.
```

Agents can run the app from the repository root with `python src/main.py`.
This starts scanning, so live sensor readings require a supported Bluetooth
adapter and nearby tags. For routine code work, prefer the mocked tests;
see [Developing without any BLE hardware](#developing-without-any-ble-hardware-at-all).

## VS Code

The checked-in `.vscode` folder provides Python test discovery and debug
launch configurations:

1. Install the Microsoft Python extension and open the repository root in
   VS Code.
2. Create the `.venv` and install dependencies using the commands above.
3. Open the Command Palette and choose **Python: Select Interpreter**;
   select `.venv` (`.venv\Scripts\python.exe` on Windows,
   `.venv/bin/python` on macOS/Linux).
4. Use **Run and Debug** and select **RuuviHub: Demo dashboard (F5)**,
   then press **F5**. VS Code opens `http://localhost:8080` when the server
   is ready. Three dummy sensors have 24 hours of seeded chart history;
   one is deliberately stale. The demo binds only to `127.0.0.1`, uses
   temporary settings/data that are removed when it stops normally, and
   starts no scanning, backfill, or cloud-upload loops. Each launch starts
   fresh and leaves your regular settings/database untouched.
   The demo reads only the datetime display preference from the project's
   `config.json`; its sensor data and other settings remain disposable.
   To debug with real sensors and your regular settings, select
   **RuuviHub: Real sensors + Browser**, which launches `src/main.py`
   with HTTPS disabled for local use. VS Code remembers the last selected
   launch configuration. Before either app launch, the **Install Python
   dependencies** task creates `.venv` if needed and installs runtime
   dependencies there. Both launches explicitly use that environment,
   even if VS Code previously selected another interpreter. The task
   requires `python3` on macOS/Linux or `python` on Windows;
   it is also available through **Terminal → Run Task**.
5. Open the Testing view to discover and run pytest tests. The workspace
   settings exclude `tests/test_connection.py`, which requires real BLE
   hardware. Install `tests/requirements.txt` first if pytest reports a
   missing test dependency.

The terminal route also works in VS Code: run `python scripts/run_demo.py`
for dummy data (optionally `--port 8081`), `python src/main.py` for real
sensors, or the pytest command in the first-code-change section. On
first app startup, the ignored `settings.local.json` and `ruuvi_data.db`
files are created in the repository root.

## 1. Architecture: four independent asyncio tasks

`src/main.py`'s `main()` starts four coroutines with
`asyncio.gather(..., return_exceptions=True)`:

| Task | Function | What it does |
|---|---|---|
| Scan | `scan_loop` | Every `scan_interval_seconds`, scans for tags (`scanner.scan`) and stores readings (`Storage.add`). |
| Upload | `upload_loop` | Every `upload_interval_seconds`, if `cloud_upload_enabled` is on, uploads pending readings (`Uploader.upload`) and prunes old DB rows (`Storage.prune`). |
| History backfill | `history_backfill_loop` | On by default (startup pass). If `history_backfill_enabled`, pulls each known tag's on-device GATT history log (`history_backfill.run_backfill_for_known_tags`) to fill gaps, on startup and/or on an interval. |
| Web | `web_server_loop` | Runs the embedded FastAPI app (`web.app.run_web_server`), which starts one or two uvicorn `Server.serve()` coroutines (HTTPS + an optional plain-HTTP redirect, see `src/web/app.py` below). |

**Crash isolation** is deliberate and layered:
- Each task's loop body is wrapped in its own `try/except Exception:
  logger.exception(...)` so an unexpected bug in one iteration is
  logged and the loop just retries next cycle instead of dying.
- `asyncio.gather(..., return_exceptions=True)` in `main()` is
  defense-in-depth: even if something escapes a loop's own try/except
  (e.g. a bug inside the except block itself), it can't cancel or crash
  the sibling tasks. `main()` logs any such escaped exception afterwards.
- `web_server_loop` additionally retries the whole web server after a
  5s delay if it ever exits, since a long-lived server task exiting is
  itself unexpected.

**Live config reload:** `scan_loop`/`upload_loop` call
`await settings_store.get()` at the top of every iteration instead of
using a config snapshot captured once at startup, so changes made from
the Config page (intervals, tag whitelist, retention,
`max_entries_per_tag`, `cloud_upload_enabled`, ...) take effect on the
next cycle with no restart. The two exceptions are `db_path` and
`log_path`, which are only read once at startup — changing where the
SQLite file or log file lives requires fully re-initializing `Storage`
or reconfiguring logging handlers, which is out of scope for live
reload (see `Storage.set_max_entries_per_tag` below for the one thing
that *is* live-resized without a restart).

**File logging:** `main.py` configures a `TimedRotatingFileHandler` (via
`configure_file_logging()`) in addition to the stdout handler set up at
import time, once `Settings.log_path` is known (default:
`logs/ruuvihub.log` next to `main.py`; rotates at midnight local time,
14 daily files kept). This runs after `SettingsStore()` bootstraps, so
the very first bootstrap log line only reaches stdout — every log line
from then on goes to both. Before attaching the handler, `main()` also
runs `_migrate_log_path_to_logs_subfolder()`, an idempotent startup
migration: if an existing `settings.local.json` has `log_path` pointing
directly at a flat file (installs from before the `logs/` folder
existed), it moves that file (and any already-rotated siblings) into a
sibling `logs/` folder and repoints `log_path` there. This runs at
application startup rather than as a shell migration step in
`scripts/update.sh` specifically so it takes effect during the very
update that introduces `logs/`, not just the one after (a shell-side
migration would rely on the previous release's already-installed
`update.sh`, which doesn't have the new logic yet).

**BLE radio serialization:** `scan_loop` (passive advertisement
scanning) and `history_backfill_loop` (GATT connection-based history
pulls) share a single `asyncio.Lock` (`ble_lock`, created in `main()`)
because scanning and GATT connections use the same BLE radio.
`scan_loop` **skips** its scan
for a cycle if the lock is already held, otherwise it holds `ble_lock`
for the *entire* scan call (not just the pre-check) so a backfill pass
can never start mid-scan; `history_backfill_loop` **waits** for the lock
instead of skipping, since scans are quick and backfill runs far less
often.

---

## 2. Module & class reference

### `src/config.py` — `Config`, `load_config()`
Loads `config.json`. Only used once, by `SettingsStore`, to bootstrap
`settings.local.json` on a fresh install (see next section) — nothing
else in the app reads `config.json` directly.

### `src/settings.py` — `Settings`, `SettingsStore`
The live runtime settings store, backed by `settings.local.json`
(git-ignored).

- **`Settings`** — a dataclass with every editable field: scan/upload
  intervals & timeout, `tag_addresses`, `max_entries_per_tag`,
  `db_path` and `log_path` (both stored but excluded from live edits —
  see above), `db_retention_days`, cloud upload fields
  (`cloud_upload_enabled`, `cloud_upload_destination`, `cloud_endpoint_url`,
  `azure_blob_container_url`, `cloud_auth_header_name`,
  `cloud_auth_header_value`), `dashboard_refresh_seconds`,
  `dashboard_show_storage_info`, `dashboard_visible_metrics`,
  `dashboard_hidden_tags`, `dashboard_change_hours`, `tag_names`
  (MAC → friendly name), file-only `datetime_format` (`fi` by default,
  or `iso`, both 24-hour), history backfill fields
  (`history_backfill_enabled`, `history_backfill_on_startup`,
  `history_backfill_periodic`, `history_backfill_interval_seconds`,
  `history_backfill_failure_threshold`, `history_backfill_disabled_tags`),
  HTTPS fields (`https_enabled`, `https_port`, `tls_cert_path`/
  `tls_key_path` — file-only like `db_path`/`log_path`,
  `https_redirect_http`), and the account/session fields (`ui_username`,
  `ui_password_hash`, `session_secret`).
- **`SettingsStore`** — async-safe (`asyncio.Lock`) load/update wrapper:
  - `get()` / `get_sync()` return an independent copy
    (`dataclasses.replace`) so callers can't mutate internal state.
  - `update(**fields)` persists to disk atomically (temp file + `os.replace`)
    and updates the in-memory copy immediately; unknown field names are
    logged and ignored rather than raising, since web form submissions
    are loosely validated.
    The temporary file is created with owner-only permissions before any
    secrets are written on POSIX; failed writes remove the temporary file
    and preserve the previous on-disk settings. On Windows, access follows
    the parent folder's ACL, so use a folder restricted to your account.
  - On construction: if `settings.local.json` exists and parses, it's
    used as-is (**never** overwritten by `config.json` afterwards). If
    it's missing, a fresh `Settings` is bootstrapped from `config.json`
    + a freshly generated `session_secret` + the default admin account.
    A corrupt existing file is backed up and startup fails closed with
    `SettingsCorruptError`; restore a good backup or explicitly remove
    the file to reset it. Corruption never silently resets the admin login.
- **`hash_password()` / `verify_password()`** — stdlib
  `hashlib.pbkdf2_hmac("sha256", ..., 260_000 iterations)` with a random
  16-byte salt, stored as `"<salt_hex>$<digest_hex>"`. No external
  dependency (no bcrypt/passlib).
- **`DEFAULT_UI_USERNAME` / `DEFAULT_UI_PASSWORD`** — `"admin"` /
  `"ruuvihub"`, the documented first-run credentials.

### `src/storage.py` — `Storage`
Two views over the data, plus a third for full history:

- **`_data`** — a bounded in-memory ring buffer per tag (`deque(maxlen=
  max_entries_per_tag)`), used by `get_all()`/`get_by_mac()` for fast
  reads. `get_latest_readings()` returns one entry per sorted buffer
  under the cache lock, so dashboard reads scale with tag count rather
  than the number of buffered readings. Backfill, tied timestamps,
  startup loading, and resizing retain measurement-time ordering.
- **Pending uploads** — SQLite rows with `uploaded = 0`, read in batches
  of at most 10,000. `get_pending()` returns a list-compatible payload with
  private row IDs; `clear_pending(batch)` acknowledges exactly those IDs.
  Pending readings and acknowledgements survive restarts. Delivery is at
  least once: a crash after remote acceptance but before acknowledgement
  can resend a batch. Legacy databases have no upload status, so migration
  queues existing history once, potentially resending previously uploaded
  readings. Configured history retention also applies to pending readings.

- **`get_history(mac, since=None, until=None, limit=None)`** — queries
  the full on-disk SQLite history directly for one tag (bypasses the
  bounded ring buffer entirely), used by the sensor history page/API and
  CSV export.
- **`get_temperature_summaries(...)`** — computes dashboard day/night
  minima and maxima in SQL and selects the closest numeric temperature
  to the configured comparison time within ±10 minutes (ties prefer the newest timestamp,
  then insertion). All tags share one connection/read snapshot and
  executor call; history payloads are not loaded into Python. Query work
  still grows with the number of readings in the selected windows.
- **`get_chart_history(...)`** — samples at most 500 records by default.
  SQL detects gaps over the complete selected range, groups gap counts
  into sampling buckets, and computes cumulative segment counters over
  those buckets. It retrieves JSON payloads for selected IDs afterward.
  Count and selection share a read transaction. Query work still grows
  with selected history; output bounds do not bound SQLite working memory.
- **`set_max_entries_per_tag(new_max)`** — the one live-editable storage
  setting that needs more than re-reading a value: it recreates each
  tag's deque with the new `maxlen`, preserving the newest readings.
  Called once per `scan_loop` iteration (a no-op if unchanged).
- **`prune(retention_days=None)`** — deletes DB rows older than the
  retention window. Accepts a live override so `upload_loop` can pass
  the current setting each cycle without reconstructing `Storage`.
- All SQLite calls (`_save_record`, `_prune_db`, `_get_history_sync`,
  `_load_from_db`) are synchronous and run via
  `loop.run_in_executor(None, ...)` so they never block the event loop.
  Each transaction explicitly closes its connection on success or failure;
  cleanup does not rely on garbage collection. This also lets Windows
  remove the disposable demo database when the server stops.

### `src/scanner.py` — `scan(config)`
Wraps `ruuvitag_sensor.RuuviTagSensor.get_data_for_sensors_async()`.
Takes any object with `.tag_addresses` and `.scan_timeout_seconds`
(duck-typed — both `Config` and `Settings` satisfy this, which is how
`scan_loop` passes a live `Settings` snapshot straight through without
scanner.py needing to import `settings.py` at all).

### `src/history_backfill.py` — GATT history backfill
Enabled by default (startup pass only; periodic backfill remains opt-in). Wraps
`RuuviTagSensor.download_history()` with a per-tag timeout controlled by
`history_backfill_timeout_seconds` (120s by default), so one unreachable
tag cannot stall a whole cycle indefinitely:

- **`merge_history_entries(entries)`** — the GATT log yields one entry
  per measurement type (temperature *or* humidity *or* pressure) with
  its own Unix timestamp; the library does not merge these itself, and
  timestamps for "the same" sample aren't guaranteed to be identical.
  This sorts entries by timestamp and greedily groups each one into the
  current group if it's within ~10s of that group's starting timestamp
  and doesn't repeat a measurement type already in the group, converting
  the group to an ISO-8601 UTC timestamp — same shape as a live scan row
  (minus battery/RSSI/acceleration, which the GATT log doesn't carry).
  Grouping by proximity (rather than independently rounding each entry to
  a fixed-width bucket) avoids splitting close entries that straddle a
  bucket boundary.
- **`backfill_tag(mac, since)`** — pulls + merges history for one tag,
  filtered to strictly after `since` (dedup against already-stored
  data). Raises `BackfillError` on any failure (BLE error, firmware
  < 3.30.0, timeout) rather than swallowing it, so the caller can count
  consecutive failures.
- **`initialize_pre_scan_backfill_cursors(settings, storage)`** — called
  once at process startup, *before* `scan_loop` runs its first
  iteration, to seed each known tag's persisted backfill cursor
  (`Storage.get_backfill_cursor`/`set_backfill_cursor`) from its current
  latest live-scan reading. This cursor is deliberately separate from
  "latest live-scan reading": if backfill used the live value directly,
  a single live advertisement received right after BLE connectivity
  resumes would make the just-ended outage gap invisible to backfill
  (scanning and backfill run independently). The cursor only ever
  advances when a backfill pass for that tag actually completes.
- **`run_backfill_for_known_tags(settings, storage, settings_store)`** —
  orchestrates a full pass: builds the eligible tag list (whitelist or
  `storage.known_macs()`, minus anything in
  `settings.history_backfill_disabled_tags`), processes tags
  sequentially (GATT connections are exclusive on a single BLE radio
  anyway) using each tag's backfill cursor (falling back to its latest
  live-scan reading if no cursor is recorded yet) as the GATT range's
  lower bound, and auto-disables a tag (persisted via
  `SettingsStore.set_backfill_disabled`) after
  `history_backfill_failure_threshold` consecutive failures — reset to
  0 on any success. On success the tag's cursor advances to when that
  pull started (not on failure, so a missed gap is retried next time).
  Recovered records are stored via the normal `Storage.add()`, so they
  behave exactly like live scans everywhere else (UI, dashboard, CSV
  export, cloud upload batch).

Backfill requires firmware 3.30.0 or newer and recovers only temperature,
humidity, and pressure. The tag retains a rolling log, so readings older
than its available history cannot be recovered. GATT reads run from the
requested start time to the end of the log; they cannot select an arbitrary
missing interval. See the upstream [GATT protocol](https://github.com/ruuvi/ruuvi-sensor-protocols/blob/56978e49afbeff2e1d19373ee05d0a419c4da0b4/gatt_formats.md)
and [library implementation](https://github.com/ttu/ruuvitag-sensor/blob/ba861e53bdb896a5d4d752e51163dc600c4543a3/ruuvitag_sensor/ruuvi.py)
for the wire format and history API.

### `src/uploader.py` — `Uploader`
Constructed with a `SettingsStore` (`Uploader(settings_store)`), so it
always reads the current cloud endpoint/auth on every call:

- **`upload(records)`** reads `cloud_upload_destination` on each call.
  `http` (the backward-compatible default) posts JSON to `cloud_endpoint_url`
  using the optional configured header. `azure_blob` creates a unique JSON
  block blob in `azure_blob_container_url`, preserving the SAS query string.
  Azure uploads require HTTP 201 and do not follow redirects or use HTTP
  endpoint credentials. Blocking requests run in the executor.
  Upload failures return `False`, leaving readings pending for retry.
  HTTP's disabled-upload/no-endpoint logging stub remains available.
- **`test_upload()`** accepts transient destination/URL overrides and sends
  a synthetic record without changing settings or the pending queue. Azure
  tests create a uniquely named `ruuvihub-test-*.json` blob. Errors shown in
  the UI and upload logs redact request URLs to keep SAS credentials private.
- `cloud_upload_destination` is validated against `http` and `azure_blob`;
  older settings files default to `http`. The Azure field accepts an HTTPS
  container SAS URL (`sr=c`), saved in the existing private settings file.

### `src/tls.py` — self-signed certificate generation
`ensure_self_signed_cert(cert_path, key_path)` — idempotent (no-op if
both files already exist, so it never invalidates a cert a browser or
operator has already trusted; raises if only one of the two exists,
rather than silently regenerating and overwriting the other). Uses the
`cryptography` package (its certificate-generation APIs don't shell out
to the OpenSSL CLI, though the package itself ships compiled Rust/
OpenSSL-backed extensions and may need a build toolchain on platforms
without a prebuilt wheel) to generate an RSA-2048 key and a 10-year
self-signed X.509 cert with SANs covering `localhost`,
`127.0.0.1`, `::1`, the machine's hostname, and its best-guess LAN IP
(found via a connect-less UDP socket trick, not real network traffic).
The private key file is created with `0600` permissions from the start
(no permissive-umask window). Called from
`web.app.run_web_server` on every startup when HTTPS is enabled and no
user-supplied cert exists yet at `tls_cert_path`/`tls_key_path`.

### `src/web/` — the embedded FastAPI app

- **`app.py`** — `create_app(storage, settings_store)` factory. Stores
  shared instances on `app.state` (`storage`, `settings_store`,
  `uploader`, `templates`, `login_throttle`, `log_stream`) instead of module globals,
  so routes access them via `request.app.state.*` and tests can spin up
  independent app instances with throwaway `Storage`/`SettingsStore`.
  Mounts `/static`, registers all routers, and defines `/healthz`.
  `run_web_server()` binds `0.0.0.0:<port>` via uvicorn's `Server` API
  (not `uvicorn.run()`) so each server is a plain awaitable that fits
  into `main.py`'s `asyncio.gather`. When `Settings.https_enabled` is
  true (the default), it resolves/generates the TLS cert (see
  `tls.py` above) and runs the real app over TLS on `https_port`; if
  `https_redirect_http` is also true, a second minimal uvicorn server
  (`create_redirect_app()` — a bare FastAPI app with one catch-all
  route) serves 308 redirects to HTTPS on the plain `port` (except for
  `/healthz`, served directly there too so older pre-HTTPS `update.sh`
  scripts can still read version JSON during an upgrade), and both
  servers run concurrently via an internal `asyncio.gather`. Setting
  `RUUVIHUB_DISABLE_HTTPS` (any of `1`/`true`/`yes`) in the environment
  forces plain-HTTP-only regardless of `https_enabled` — used by the
  VS Code debug configs in `.vscode/launch.json` so local debugging
  never has to click through a self-signed cert warning.
- **`auth.py`** — `router` with `GET/POST /login`, `POST /logout`.
  Session cookie is signed with `itsdangerous.URLSafeTimedSerializer`
  keyed on `Settings.session_secret`, 30-day max age, `httponly`,
  `samesite=lax`. `LoginThrottle` implements the escalating-delay
  throttle (1s per consecutive failure, capped at 10s, reset on
  success) — lives on `app.state` so each app instance/test has its
  own. `require_login_page` (redirects to `/login`) and
  `require_login_api` (401 JSON) protect admin-only Config pages and
  actions; the dashboard and sensor history are public, read-only
  routes. `require_login_ws` is the WebSocket
  equivalent used by `logstream.py`'s `/ws/logs` route (a WS handshake
  can't be redirected or answered with a 401, so the route itself
  closes the connection when this returns `None`).
- **`dashboard.py`** — `GET /` and `GET /api/current`. Uses
  `Storage.get_latest_readings()` to build one latest-reading card per
  tag without copying/scanning all buffered readings, flags a card stale
  if its reading is older than 2x `scan_interval_seconds`. Friendly-name
  lookup builds one normalized name map per request, preserving the first
  saved name for equivalent keys and matching MACs case/separator-insensitively
  via `mac_utils.compact_upper()` (see below) so a name saved when a
  tag's MAC was recorded in a different textual shape still resolves.
  Temperature cards also show signed temperature change and day/night
  minima and maxima from SQL summaries. Change defaults to one hour and
  can compare 1–168 hours ago. It displays `—` without
  a comparison within ±10 minutes of the chosen time, or a valid current
  reading within the last 10 minutes. Day runs from 07:00 to 22:00 in the
  server's local timezone; night shows the current night or the most
  recently completed night. Each local boundary is converted separately
  to UTC so overnight daylight-saving changes use the correct offsets.
  Sensor cards appear first; optional storage metadata appears as small,
  inline text in the footer, with no separate heading or panel.
  The footer sits at the bottom of short pages and follows the content
  on longer pages without covering sensor data.
  It includes the running release version (`dev` for a source checkout).
  Hidden sensor MACs are matched case/separator-insensitively before
  summary queries. `GET /api/current` includes `dashboard_options` with
  visible metrics, comparison hours, and refresh seconds. Cards return
  `temperature_change` for the selected period. Polling applies new
  preferences to existing nodes and adjusts its timer without a reload.
  Shared timestamp formatting applies the configured Finnish or ISO
  display to initial HTML, dashboard polling, charts, and live logs.
  Templates version changed styles/scripts by file modification time to
  prevent cached assets from retaining old display behavior.
- **`sensor.py`** — `GET /sensor/{mac}`, `GET /api/history/{mac}`
  (JSON, simple downsampling for large ranges), `GET
  /api/history/{mac}/export` (raw CSV, no downsampling). All three
  resolve the URL's `mac` via `_resolve_mac()`/`mac_utils.resolve_known_mac()`
  against the union of `Storage.known_macs()` and `Settings.tag_names`
  keys before looking anything up, so a MAC typed/copied in a different
  textual shape than however it's actually stored (e.g. compact
  lowercase hex vs. canonical uppercase colon-separated) still finds the
  right history rows and friendly name instead of silently returning
  nothing.
  The chart request adds `?chart=true` to receive `{records, since, until}`.
  The bounds cover the full selected time window through the server's current
  time, including empty periods. Each record's `_chart_segments` tracks gaps
  separately for each metric before downsampling. The browser plots epoch
  timestamps on a linear axis and breaks lines when segment IDs change;
  missing values stay blank. GATT history's five-minute cadence is accounted
  for separately from live scans.
- **`mac_utils.py`** — shared helpers for the MAC-format reconciliation
  described above: `compact_upper()` (comparison key), `canonicalize_mac()`
  (best-effort uppercase colon-separated form for a never-before-seen
  MAC), and `resolve_known_mac()` (resolve to an existing key if one
  matches, else fall back to `canonicalize_mac()`). Used by both
  `dashboard.py` and `sensor.py` so both stay consistent.
- **`config.py`** — `GET/POST /config`, `POST
  /api/config/test-upload`. Notable behaviors: the cloud auth header
  *value* input is always left blank on page load and an empty
  submission means "keep the existing secret" (never overwritten with
  blank); a blank tag friendly-name removes that MAC from `tag_names`
  entirely so the mac-as-fallback logic (`tag_names.get(mac, mac)`)
  keeps working elsewhere; password fields are only applied if a
  non-empty new password was submitted and it matches the confirmation
  field; `db_path` has no input on this page at all (file-only).
- **`dashboard_config.py`** — admin-only `GET/POST /config/dashboard`.
  Saves only dashboard preferences, with CSRF protection and validation.
  Metrics can all be deselected; new sensors remain visible when they
  arrive after a form was loaded. System saves do not change dashboard
  preferences. The configuration pages share navigation between system
  and dashboard settings.
- **`templates/`** — `base.html` (shared layout + admin-only
  "using default credentials" reminder, Settings menu linking directly to Dashboard settings and System settings, Logs toggle,
  logout form, and slide-in `#log-panel` drawer; public pages show an
  admin login link instead). `login.html` hides the shared navigation.
  `base.html`'s `<head>` also has a small synchronous
  (non-`defer`, non-`async`) inline `<script>` that reads
  `localStorage["ruuvihub.logPanel.open"]` and adds a `log-panel-open`
  class to `<html>` before first paint — this is what prevents the
  drawer from flashing closed-then-open on every navigation, since the
  `defer`red `logstream.js` only runs after first paint. A second tiny
  inline script right after the checkbox sets its initial `.checked`
  from that same `<html>` class (not a second `localStorage` read) so
  the two inline scripts can't disagree. `login.html`, `dashboard.html`,
  `sensor.html`, `config.html`, `dashboard_config.html`, and their shared
  `config_nav.html` navigation.
- **`static/`** — `style.css` (minimal, no framework; `--topbar-height`
  custom property sizes the sticky `.topbar` and is also used as
  `.log-panel`'s `top` offset so the drawer starts exactly below the
  topbar instead of overlapping it; `.log-panel`'s width comes from
  the `--log-panel-width` custom property, draggable via the
  `#log-panel-resize-handle` strip on its left edge),
  `chart.umd.min.js` (Chart.js v4.4.4, vendored — no CDN, works
  offline on a Pi), `app.js` (shared `fetchJSON`/`startPolling`/
  `formatTimestamp`/`timeAgo` helpers), `logstream.js` (drives the log
  panel: connects to `/ws/logs`, client-side level filtering, and
  auto-reconnect with backoff while the panel is open; persists
  open/closed state, the selected level, and the dragged width in
  `localStorage` under `ruuvihub.logPanel.open`/`.level`/`.width`,
  synced across tabs via the `storage` event (width is also
  pre-applied by the same pre-paint inline script in `base.html` that
  sets the open-state class, avoiding a resize flash on navigation);
  caps the DOM at `MAX_RENDERED_LINES`
  (500) rows, dropping the oldest so long sessions don't bloat the
  page; on load, `setOpen()` also removes the pre-paint
  `log-panel-open` class from `<html>` as its first action, becoming
  the sole runtime owner of the drawer's open/closed state), plus one
  JS file per page (`dashboard.js`, `sensor.js`, `config.js`).
  Public static assets of at least 1,000 bytes are gzip-compressed at
  level 6 when the client accepts gzip; conditional cache revalidation
  remains available. Compression is scoped to `/static`. Sensor charts
  disable animations and parsing of their numeric `{x, y}` data while
  retaining point markers, tooltips, and null gap markers.
  Dashboard polling reuses cards by MAC and updates changed text/stale
  state, adds/removes cards as needed, and follows API sort order. An
  unchanged refresh retains its DOM nodes and keyboard focus.
- **`logstream.py`** — `LogStreamHandler`, a `logging.Handler`
  attached to the root logger (via `attach_to_root()`, called once
  from `create_app()`) that keeps the last `LOG_BUFFER_SIZE` (200)
  records in an in-memory ring buffer and fans new ones out to any
  connected WebSocket subscriber. `attach_to_root()` first detaches any
  previously-attached `LogStreamHandler` (removing it from the root
  logger and clearing its subscribers) so repeated `create_app()`
  calls (every test in the suite) never stack duplicate handlers or
  leak orphaned subscriber queues on the root logger. `router`'s
  `GET /ws/logs` (a WebSocket route) calls `require_login_ws()` and
  closes the socket (code 1008) before accepting if not logged in,
  otherwise calls `subscribe_with_snapshot()` (captures the backlog
  and registers the subscriber under one lock hold, so no record is
  ever duplicated between the backlog and the live queue, or dropped)
  and sends a `{"type": "backlog", ...}` message with that buffer
  followed by `{"type": "log", "entry": ...}` messages as new records
  arrive. Note: `main.py`'s `logging.basicConfig(level=logging.INFO,
  ...)` sets the root logger's level, which this module does not
  change — DEBUG-level records are only ever fanned out if something
  else lowers that level, since a handler's `emit()` only runs for
  records the logger already decided to pass through.

---

## 3. Local development & debugging

### Running the app locally

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   |   Linux: source .venv/bin/activate
pip install -r requirements.txt
python src/main.py
```

On first run this creates `settings.local.json` (bootstrapped from
`config.json`) and `ruuvi_data.db` next to `main.py`. Delete
`settings.local.json` to reset all web-UI-editable settings (including
your login) back to the `config.json` defaults + a freshly generated
session secret — this does **not** touch `ruuvi_data.db`.

### Running tests

```bash
cd tests
python -m venv .venv
# Windows: .venv\Scripts\activate   |   Linux: source .venv/bin/activate
pip install -r requirements.txt
cd ..
python -m pytest tests/ -v
```

Only one test file is expected to fail if a dependency/hardware
prerequisite is missing — this is normal, not a regression:

| File | Why it might fail |
|---|---|
| `test_connection.py` | A manual, live integration test — requires an actual USB/onboard Bluetooth adapter and a RuuviTag in range. `test_scanner.py` (mocked BLE library, no hardware needed) runs fine on Windows and Linux alike, since `ruuvitag-sensor` installs on both (via `bleak`/WinRT on Windows, `bluez`/`bleak` on Linux). |

Run everything else with:

```bash
python -m pytest tests/ -q --ignore=tests/test_connection.py
```

For a repeatable UI storage/asset baseline on Linux or macOS, run from
the repository root (Python standard library only):

```bash
python scripts/benchmark_ui.py --rows 1000 --repetitions 30 > baseline-1k.json
python scripts/benchmark_ui.py --rows 100000 --repetitions 30 > baseline-100k.json
```

Each invocation creates and removes its own synthetic SQLite database;
it does not access the deployed database or BLE hardware. Run each size
in a fresh process. Results include fixed-date chart-history timings,
response sizes, asset gzip level 6 estimates matching static serving,
and process lifetime peak RSS
(including fixture creation, not isolated query memory). This is not an
HTTP/browser benchmark. For deployment comparisons, repeat the same sizes
on the target Pi, record the hardware and revision, and measure authenticated
HTTP response times separately. Include concurrent dashboard/history requests
and browser responsiveness; synthetic query timings alone do not establish
the deployed user experience.

Measure dashboard server construction separately (app dependencies required):

```bash
python scripts/benchmark_dashboard.py --tags 1 10 50 --buffered 100 1000 --repetitions 30
```

For optional browser rendering checks and render-plus-layout timings,
install Playwright in a development environment, then run:

```bash
python -m pip install playwright
python -m playwright install chromium
python scripts/benchmark_dashboard_browser.py --tags 1 10 50 --repetitions 30
```

This verifies node reuse, unchanged-refresh focus, safe text rendering,
metric/stale/name updates, ordering, removal, and empty-state recovery.
Playwright is optional tooling, not a runtime or normal CI dependency.
To compare an old dashboard script, pass `--dashboard-js PATH --skip-checks`
(the old renderer fails the node-preservation checks).

### Developing without any BLE hardware at all

`ruuvitag-sensor` (via `bleak`) installs and imports fine on both
Windows and Linux, and real scanning works on both once you have a
Bluetooth adapter and at least one RuuviTag in range. But if you're
developing without any adapter/tag at hand (e.g. in CI, or a machine
with Bluetooth disabled), every test file that touches
`main.py`/`scanner.py` (e.g. `test_main.py`, all the `web/*` test files)
works around that by injecting a fake module into `sys.modules`
**before** importing anything that transitively imports
`ruuvitag_sensor`:

```python
import sys, types

if "ruuvitag_sensor" not in sys.modules:
    fake_pkg = types.ModuleType("ruuvitag_sensor")
    fake_ruuvi_mod = types.ModuleType("ruuvitag_sensor.ruuvi")

    class _FakeRuuviTagSensor:
        @staticmethod
        async def get_data_for_sensors_async(macs, timeout):
            return {}

    fake_ruuvi_mod.RuuviTagSensor = _FakeRuuviTagSensor
    sys.modules["ruuvitag_sensor"] = fake_pkg
    sys.modules["ruuvitag_sensor.ruuvi"] = fake_ruuvi_mod

import main  # now safe to import, even without a real BLE stack installed
```

Copy this pattern (see `tests/test_main.py` for the canonical version)
whenever you need to run/import `main.py` without a Bluetooth adapter
or without `ruuvitag-sensor`/`bleak` installed at all.

### Testing on Linux via Docker (optional)

Not required for BLE itself (that works on Windows directly), but
useful to sanity-check Linux-specific behavior (e.g. before deploying to
a Pi) or to reproduce a Linux-only bug without a second machine:

```bash
docker run --rm -it -v "${PWD}:/app" -w /app python:3.12-slim bash
# inside the container:
apt-get update && apt-get install -y build-essential libglib2.0-dev
pip install -r requirements.txt
pip install -r tests/requirements.txt
python -m pytest tests/ -q --ignore=tests/test_connection.py
```

Actual BLE scanning still needs a Bluetooth adapter passed through to
the container (not covered here — for real hardware testing on Linux,
prefer running directly on a Pi per [README-pi.md](README-pi.md)).

### Inspecting the SQLite database

```bash
sqlite3 ruuvi_data.db "SELECT mac, timestamp, data FROM readings ORDER BY id DESC LIMIT 10;"
```

See [README-windows.md](README-windows.md#6-inspecting-stored-data-sqlite)
or [README-pi.md](README-pi.md#7-inspecting-stored-data-sqlite) for more
query examples and how to install the `sqlite3` CLI.

### Release process (CI/CD)

GitHub Actions workflows (`.github/workflows/`) automate testing,
versioning, packaging, and code scanning (see
[README-pi.md](README-pi.md) for the install/update side):

| Workflow | Trigger | What it does |
|---|---|---|
| `ci.yml` | Main pushes, pull requests to `main`, or manual dispatch | Runs the hardware-free tests on Ubuntu and Windows with Python 3.11. Doesn't tag or publish anything. |
| `autotag.yml` | Push to `main`, when `AUTO_RELEASE_ENABLED` is `true` | Re-runs tests, selects the next stable patch tag with `scripts/next_release_tag.py`, pushes it, and directly invokes `release.yml`. The minimum version is `v1.1.0`; normal patch bumps follow it. A concurrency group serializes runs; commits that are no longer the tip of `main` or already have a stable release tag are skipped. |
| `release.yml` | Push of a `v*` tag, or invoked by `autotag.yml` | Re-runs tests, builds the archive with `scripts/build_release.py`, and publishes it as a GitHub Release. Version input must be a stable `vX.Y.Z` tag. The archive includes source, scripts, bootstrap config, runtime requirements, `README-pi.md`, a generated `VERSION`, the hardware check as `scripts/test_connection.py`, and third-party license notices. Root `LICENSE` and `SECURITY.md` are required inputs. Local runtime state and bytecode are excluded; private files nested inside packaged folders fail the build. |
| `codeql.yml` | Push/PR to `main`, weekly, or manual | Scans Python and JavaScript once the repository is public. Skips while private on the current plan. |

For a bigger bump than the automatic patch increment, push a `vX.Y.0`
(minor) or `vX.0.0` (major) tag yourself — `autotag.yml` always reads the
highest existing tag as its baseline, so the next auto patch-bump picks
up from there.

Build and inspect the exact release artifact locally without publishing:

```bash
python scripts/next_release_tag.py --minimum-version v1.1.0
python scripts/build_release.py v1.1.0 --output-dir dist
tar -tzf dist/ruuvihub-v1.1.0.tar.gz
```

Run this before tagging. Creating the feature branch or building locally
does not publish a release. Automatic main releases remain off until the
repository Actions variable `AUTO_RELEASE_ENABLED` is set to `true`.
For a fresh repository, finish security settings and CI, manually tag the
reviewed initial commit as `v1.1.0`, then enable automatic patch releases.

**One-time repo setup:** `autotag.yml` needs to push tags and
`release.yml` needs to publish releases, both using the built-in
`GITHUB_TOKEN`. Keep repo **Settings → Actions → General → Workflow
permissions** at read-only, with Actions approval of pull requests disabled.
Only the tagging and publishing jobs request `contents: write`; test jobs
remain read-only. The reusable release workflow receives no inherited
repository secrets. External Actions are pinned to commit hashes.

`.github/dependabot.yml` schedules weekly Python and Actions updates. Python
updates are grouped by dependency across the root and test requirements, so
shared requirements move together in one PR per package. Enable Dependabot
alerts/security updates in repository settings. After making
the repository public, enable secret scanning, push protection, and GitHub
private vulnerability reporting (the route chosen in [SECURITY.md](SECURITY.md)).
Protect `main`: require pull requests and both `Tests (ubuntu-latest)` and
`Tests (windows-latest)`, and block force pushes and deletion. These settings
are separate from the checked-in workflows. See [release maintenance](docs/releases.md)
for the initial setup and ongoing release procedure.

### Resetting the web UI without touching your readings

Stop the service, delete `settings.local.json`, start it again — this
regenerates it from `config.json` with a fresh session secret and the
default admin account, without touching `ruuvi_data.db`.

### Adding a new web UI page

Follow the pattern in `src/web/dashboard.py`/`sensor.py`/`config.py`:

1. Create `src/web/<name>.py` with `router = APIRouter()`.
2. Decide whether each route is public read-only or admin-only. Protect
   admin-only HTML pages with `Depends(require_login_page)` and JSON
   endpoints with `Depends(require_login_api)` (both from `web.auth`),
   and read shared state via `request.app.state.storage` /
   `.settings_store` / `.uploader` / `.templates`.
3. Add a template under `src/web/templates/` extending `base.html`.
4. Register it in `src/web/app.py`: one `from web.<name> import router
   as <name>_router` import and one `app.include_router(<name>_router)`
   call, alongside the existing ones.
5. Add `tests/test_<name>.py` following the existing test files'
   pattern: build a throwaway `Storage`/`SettingsStore` pointed at
   `tmp_path`, `create_app(storage, settings_store)`, wrap in FastAPI's
   `TestClient`; log in via `client.post("/login", data={"username":
   DEFAULT_UI_USERNAME, "password": DEFAULT_UI_PASSWORD})` for
   admin-only routes, and test public routes without a session (cookies
   persist automatically across requests on the same `TestClient`).
