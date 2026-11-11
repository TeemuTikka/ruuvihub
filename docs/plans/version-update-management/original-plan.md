# RuuviHub software version and update management

## Goal and confirmed scope

Notify administrators when a new release is available and support explicitly
approved installation from the web UI on Raspberry Pi and Windows.
Make code-only updates fast by avoiding unnecessary Python dependency installation.
Windows must support both a normal installed background application and development
checkouts; development checkouts receive notifications but are not self-updated.
Automatic unattended installation is outside the initial scope.
Reverting to the previous working version after a failed update is mandatory
on both Pi and Windows, not an optional follow-up feature.
Windows and Linux/Raspberry Pi may use different packaging formats and runtime
distribution strategies. Share application version semantics and update safety
contracts, not necessarily installation artifacts or package implementations.

## Current state

- `scripts/update.sh` stages versioned releases, builds a fresh environment for
  every application version, switches release/environment links, checks systemd
  and the version returned by `/healthz`, and rolls back on activation failure.
- `scripts/lib.sh` implements `build_release_venv`: create the environment,
  upgrade pip, and install `requirements.txt` on every release.
- `requirements.txt` specifies minimum versions rather than a reproducible,
  complete locked dependency set.
- `scripts/build_release.py` builds a source archive without a Python runtime or
  virtualenv. It excludes runtime state and includes installation scripts,
  configuration, version metadata, and license notices.
- `.github/workflows/release.yml` tests on Ubuntu and publishes that archive.
  Windows production packaging and lifecycle management do not yet exist.
- `src/version.py` reads the packaged `VERSION`, falling back to `dev` for source
  checkouts. The running version is exposed by the UI and `/healthz`.
- Pi deployments separate persistent settings, SQLite, logs, and certificates
  into `shared`; environments currently live under `shared/venvs/<version>`.
- Existing management scripts refresh only after a successful update. A transition
  cannot assume the new updater is already present when the old script starts.
- Reviewed `README.md`, `README-pi.md`, `README-windows.md`, relevant portions of
  `README-dev.md`, and `docs/releases.md`.

## Proposed architecture

### Release identity and dependencies

Publish a versioned release manifest describing application version, source commit,
supported targets and Python compatibility, dependency-lock identity, artifact
sizes/checksums, and manifest/updater compatibility.
Publish platform-specific artifacts under the same application release version,
with equivalent application functionality and required legal notices.

### Platform-specific packaging

| Target | Recommended initial direction | Packaging flexibility |
|---|---|---|
| Linux targeting Raspberry Pi | Lightweight `.tar.gz` application archive, system Python, immutable dependency environments, and systemd integration | Add target-specific wheel bundles for supported ARM architectures/Python versions when needed. Preserve the existing archive-update bootstrap path. |
| Windows installed application | Dedicated Windows artifact and lifecycle adapter; select ZIP with local Python versus bundled-runtime installer during contract definition | A frozen executable or installer is allowed if it improves installation and background operation. It need not mirror the Pi's source/venv layout. |
| Windows development checkout | Source checkout and developer environment using the shared dependency workflow | Keep F5/debug workflows and notification-only behavior; do not treat development checkouts as installer-managed deployments. |

Use one release tag/version for both platforms. Artifact names and manifest
entries distinguish OS, architecture, packaging kind, runtime requirements,
and package revision where needed; package revisions do not create divergent
application version histories. The checker selects an explicitly compatible
artifact, never one inferred solely from its filename. Do not offer installation
for a platform whose artifact has not been published and validated.

Keep the update engine's transaction/state model, release validation, and UI
contract shared. Delegate preparation, runtime selection, activation, and
restoration to packaging/platform adapters. For Pi source packages, rollback
restores code plus dependency environment; for bundled Windows packages, it
restores the previous application/runtime bundle. Do not force Windows bundles
through pip or require Pi-specific links, Bash, or systemd on Windows.

Build and validate artifacts in platform-appropriate CI jobs. Each manifest entry
includes its own checksum, compatibility and minimum-updater requirements.
Versioned Windows bundles must remain restorable without an in-place installer
destroying the previous working version. Runtime/environment reuse requirements
apply to source-based packages; bundled packages instead prioritize avoiding
unnecessary downloads where practical while preserving rollback.

### Dependency and version tooling

Use `pyproject.toml` as the authoritative source for Python compatibility,
runtime dependencies, and separate development/test dependency groups.
Recommend uv for development/CI lock generation, with a committed `uv.lock`.
Prove resolution and exported installation on the supported Windows/Pi matrix
before adopting it. A universal lock records platform alternatives; it does not
prove that compatible wheels exist or that the application runs on every target.

For source-based deployments, initially export complete, exact, hash-pinned
deployment requirements from the lock and retain pip on installed devices,
enforcing `--require-hashes`. Build bundled Windows artifacts from the same
reviewed lock inputs in CI; end users of those bundles need not run pip or uv.
Do not maintain exported requirements independently or resolve newer dependencies
during installation. CI checks the lock against project metadata and verifies
exports. Dependency upgrades happen in reviewed changes, not incidentally during
application updates. Keep the existing developer commands working or explicitly
update their documentation when switching to the new workflow.

Keep the release tag as the version source of truth. Generate `VERSION`,
manifest version, and any Python distribution version from that source in CI;
test their agreement rather than manually maintaining duplicate versions.
If the application later becomes an installed Python package, use distribution
metadata for runtime identification, without requiring that packaging refactor
for the initial archive-based updater.

Key environments by locked dependency content plus interpreter compatibility,
OS, and architecture, not application version. Retained releases record their
environment identity. Never mutate an environment referenced by a release, copy
virtualenvs between machines, or relocate a prepared virtualenv.
Create new environments at their final paths and publish readiness only after
successful installation and validation. Reuse verified, ready environments for
code-only releases. Cache wheels for dependency-changing releases.
Compute environment identity from canonical runtime dependency inputs and
interpreter/platform compatibility, excluding application version and unrelated
test/dev changes. Record the installer policy used to prepare an environment.

Launch production services with the selected environment's Python directly.
Never use an auto-syncing `uv run` or `uv sync` against a retained/active
environment: `--locked` prevents lockfile changes but does not prevent environment
mutation. Keep application code outside shared dependency environments.

### Release notifications

Add a bounded background checker using the public GitHub Releases API:
startup check, approximately daily checks with jitter, conditional requests,
timeouts, backoff, and explicit handling of offline/rate-limit failures.
Stable releases only initially; use `packaging.version.Version` for comparisons
and separately validate the permitted `vX.Y.Z` release-tag format.
Do not block scanning, uploading, backfill, or startup on network requests.

Show signed-in administrators current/latest versions, compatibility,
release-note links, last-check time/status, and a manual Check now control.
Provide a persisted setting to disable automatic checks.
Development checkouts report `dev`, show release information, and never offer
self-installation. An incompatible release is informational, not installable.

### External updater

Implement a shared Python update engine outside the application process, with
small Pi/systemd and Windows lifecycle adapters. Keep the web application
unprivileged. Its authenticated, CSRF-protected update action submits only a
restricted release request; the updater independently validates version,
source, compatibility, and artifact identity.

Use durable state outside release directories and a per-installation lock:
idle, downloading, preparing, activating, verifying, succeeded, rolled back,
or failed/recovery-required. Browser reloads and restarts must not lose status.
Define recovery for interruption at each activation boundary.

Update transaction:
1. Validate request, updater compatibility, target compatibility, and disk space.
2. Download over verified TLS; validate size/checksum and safely extract without
   traversal, escaping links, or excessive extraction.
3. Stage application files and reuse/build the immutable dependency environment
   while the current application continues collecting readings.
4. Perform preflight checks and any required consistent backups.
5. Stop the application and switch its release/environment selection using an
   OS-appropriate activation mechanism.
6. Restart and check expected `/healthz` version plus service/process health.
7. Roll back the previous application/environment selection on activation
   failure, and report rollback failure distinctly.
8. Prune only unreferenced environments and old releases, protecting the active
   and rollback versions.

### Required rollback behavior

- Before activation, durably record and retain the last known working release,
  its exact environment, and its launch configuration.
- Download or preparation failures leave that version running. Activation,
  startup, or expected-version health-check failures automatically restore it
  and verify that it is healthy.
- Interrupted activation resumes recovery using the durable transaction record;
  it must not depend on the failed application's web server or environment.
- Provide an explicit Revert to previous version admin action for an update
  that passed startup checks but subsequently proves faulty. Keep an independent
  local rollback command on both platforms when the dashboard is unavailable.
- Retain the previous working release until a later successful activation
  establishes a new rollback target. Repeated failed attempts must not replace
  it with a failed release or allow pruning to delete it.
- Only offer normal reversible updates when the previous release can still read
  persisted settings/data. Block incompatible updates from this flow until an
  explicit migration/recovery procedure is available; a backup alone does not
  guarantee safe automatic rollback.
- Record update failure and rollback outcome separately. If restoration fails,
  preserve both releases and diagnostics and report recovery-required rather
  than claiming the system is healthy.

Checksums initially detect corruption; document that they are not independent
publisher authentication. Keep signed manifests as a future prerequisite for
unattended installation, not an implicit property of checksum verification.

### Persistence, migrations, and platform lifecycle

Preserve settings, database, logs, and TLS certificates separately from code.
Code/environment rollback is not data rollback. Prefer backward-compatible
migrations; incompatible changes require explicit backup/recovery handling.
Never silently restore an older database and discard newly collected readings.

Keep systemd for Pi. For Windows, select and document a service/launcher model
with explicit account, elevation, writable-data-directory, ACL, restart, and
uninstall behavior before implementation of that adapter. Do not rely on Windows
symlink privileges or moving files that the running process has locked.
Keep source-checkout/F5 workflows unchanged.

## Other version-management scenarios and alternatives

These are evaluated options, not additional commitments. Keep a single primary
workflow rather than supporting multiple packaging systems without a clear need.

| Scenario or approach | When it fits | Tradeoffs and planned position |
|---|---|---|
| Recommended: uv locks, pip deployment, lightweight archives | Existing Pi installations and Windows with supported Python | Reproducible dependency preparation with minimal device-side tooling changes; code-only releases reuse environments. Default initial approach. |
| uv on deployed devices as well | Supported targets where installation speed and Python provisioning justify it | Can simplify lock-based installation and improve preparation speed, but adds a tool/runtime lifecycle. Validate platform support and bootstrap integrity before replacing pip; still retain immutable environments and external rollback. |
| pip-tools with hash-pinned requirements | uv compatibility or maintenance constraints prevent adoption | Conservative pip-based alternative; account for per-platform/Python resolution differences. Prefer this fallback over ad hoc `pip freeze` from a developer machine. |
| Prebuilt dependency wheelhouses | Offline sites, missing public wheels, or expensive Pi compilation | Prepare and test bundles per compatible OS/architecture/Python target; install with verified hashes without reaching package indexes. Transfer only when dependency identity changes. Not a portable virtualenv. |
| Standard Python wheel plus pipx/uv tool | A future installable CLI/package distribution | Standardizes entry points and metadata, but requires package-layout/data-file work and does not provide service orchestration, data migration, or transactional rollback by itself. Evaluate separately, not required initially. |
| Frozen Windows application/installer | End users must not install Python | Valid Windows-specific packaging choice, independent of Pi packaging. PyInstaller or similar tooling requires target builds, BLE/native-library/resource tests, signing decisions, and an external updater. Choose against ZIP/local-Python packaging during contract definition. |
| Containers | Managed Linux/server deployments with an established container stack | Image rollback is convenient, but BLE/BlueZ access, privileges, persistent volumes, and Windows native BLE complicate parity. Not the default home Pi/Windows solution. |
| OS-native packages or managed deployment | Fleet/enterprise installs | Windows installers/package managers or Linux packages integrate with their ecosystems, but increase packaging and lifecycle maintenance. Consider when device fleet requirements justify them. |
| Git checkout updates | Developers and contributors | Keep normal Git and `.venv` workflows; notification-only in the app. Do not have a production updater overwrite dirty checkouts or perform `git pull` inside a running service. |
| Prerelease/beta channels | Opt-in testers after stable updates are reliable | Separate channel selection and prerelease filtering; never move stable users automatically to beta or downgrade implicitly. Deferred. |
| Skipped releases and updater upgrades | Devices that update infrequently | Manifest declares minimum updater and data-schema compatibility. Support direct jumps only when validated; otherwise show a required bridge release/bootstrap procedure. |
| Python or OS upgrades | Interpreter ABI, supported OS, or architecture changes | Treat these as distinct runtime/platform transitions, not ordinary dependency refreshes. Prepare a compatible environment and preserve a usable previous runtime when rollback is promised; otherwise require explicit operator-managed upgrade. |
| Offline/manual updates | No outbound GitHub access or restricted networks | Keep local verified archive/bundle installation and rollback. Web notifications may be unavailable; this must not disable sensor operation or local management. |
| Withdrawn or faulty releases | A published release is later found defective | Stop offering known withdrawn releases when metadata identifies them, retain diagnostics, and permit explicit rollback to a compatible retained version. Never silently force a downgrade. |

## Web research supporting the recommendation

- Python Packaging User Guide, `pyproject.toml`:
  https://packaging.python.org/en/latest/guides/writing-pyproject-toml/
- Python Packaging User Guide, single-sourcing versions:
  https://packaging.python.org/en/latest/discussions/single-source-version/
- uv project structure, universal lockfile, and export formats:
  https://docs.astral.sh/uv/concepts/projects/layout/
- uv lock/sync behavior (`--locked` is not environment immutability):
  https://docs.astral.sh/uv/concepts/projects/sync/
- pip repeatable installs, transitive pins, hashes, and target-specific wheelhouses:
  https://pip.pypa.io/en/stable/topics/repeatable-installs/
- pip hash-checking requirements and binary-only installation:
  https://pip.pypa.io/en/stable/topics/secure-installs/
- Python virtual environments are disposable, not movable/copyable:
  https://docs.python.org/3/library/venv.html
- Standard Python version parsing/comparison:
  https://packaging.pypa.io/en/stable/version.html

These sources support packaging/dependency decisions, not a claim that pip or uv
provides application-service rollback. Transactional activation, health checks,
data compatibility, and rollback remain RuuviHub responsibilities. Wheel
availability and uv binary support for the exact Pi targets remain to be verified.

## Implementation todos

1. Define release and platform contracts.
   Finalize manifest schema, supported OS/architecture/Python matrix, lock
   generation approach, Windows background-service model, and persistence layout.
   Explicitly select packaging independently for Windows and Linux/Pi while
   preserving one application version and shared update/rollback contracts.
   Confirm Windows packaging/runtime expectations and older Pi support before
   committing to platform artifacts.
2. Add reproducible dependency and artifact metadata.
   Add `pyproject.toml`, committed `uv.lock`, generated hash-pinned deployment
   exports and CI consistency checks; validate the pip-tools fallback if needed.
   Update `requirements.txt` workflow, extend
   `scripts/build_release.py`, and update `.github/workflows/release.yml` to
   publish compatible manifests, checksums, and Windows artifacts.
3. Implement immutable environment reuse for Pi updates.
   Change `scripts/lib.sh`, `install.sh`, `update.sh`, and `rollback.sh` together;
   track release/environment mappings, preserve legacy installation rollback,
   cache downloads, and prune by references rather than release names.
4. Add notification service and administration UI.
   Extend `src/version.py` or add a focused checker module, wire lifecycle into
   `src/main.py`, add settings/config validation and admin routes/templates,
   and keep the latest known result separate from the current check status.
5. Build the shared external updater and CLI.
   Implement verified staging, locking, durable state, preparation, activation
   hooks, expected-version checks, rollback, recovery, and safe retention.
6. Integrate Pi lifecycle and bootstrap.
   Install the updater/control boundary, migrate current installations without
   depending on refreshed scripts too early, and retain manual/offline archive
   updates and rollback commands.
7. Add Windows production install/update/rollback.
   Implement the selected background lifecycle adapter and installation layout,
   shared-state ACLs, restart/health checks, recovery, and uninstall behavior.
   Reuse the shared engine rather than duplicating update logic in PowerShell.
8. Connect web-approved installation on both platforms.
   Add capability detection, authenticated/CSRF-protected requests, duplicate
   request prevention, progress/result display, Revert to previous version,
   and reconnect-after-restart UX.
   Unsupported/dev installations expose notification-only behavior.
9. Complete cross-platform acceptance and operator documentation.
   Exercise transition installs, rollback/recovery, and offline behavior; update
   `README.md`, `README-dev.md`, `README-pi.md`, `README-windows.md`, and
   `docs/releases.md` in the same changes that introduce their behavior.

## Delivery order

- First: contracts, reproducible metadata, and fast Pi code-only updates.
- Second: release notifications, independently useful without self-installation.
- Third: shared updater and Pi bootstrap.
- Fourth: Windows production lifecycle and packaging.
- Fifth: web-approved installation across both platforms.

Wheel bundles built in CI are conditional follow-up work if cached,
dependency-changing installations remain too slow. Containers, frozen
executables, delta updates, prerelease channels, and unattended scheduling are
not initial deliverables.

## Validation and acceptance

- Code-only updates run no pip dependency installation.
  This applies to source-based deployments; bundled Windows deployments require
  no device-side Python dependency installation at all.
- Validate platform artifact selection and reject wrong OS/architecture/package
  kinds before activation. Test independent Pi and Windows packaging paths and
  restoration of the previous bundled runtime where Windows uses one.
- Identical lock content with incompatible interpreters/platforms never shares
  an environment; changed dependencies never modify the active environment.
- Application-version changes and test/dev-only dependency changes do not
  invalidate an otherwise identical runtime environment.
- CI validates lock freshness, exported hashes, supported-target installs, and
  agreement between tag, packaged VERSION, and manifest version.
- Offline/check/download/preparation failures leave collection running.
- Test numeric ordering, `dev`, prereleases, malformed responses, caching,
  rate limits, disable/manual-check behavior, and incompatible releases.
- Test authenticated/CSRF-protected controls and restricted updater requests.
- Test corrupt archives, unsafe paths/links, insufficient space, failed
  preparation, concurrent requests, interruption, expected-version mismatch,
  failed restart, successful rollback, and rollback failure.
- On Pi and Windows, prove automatic restoration of the previous working
  release/environment after failed activation, manual reversion after a
  superficially successful update, and local rollback without a working web UI.
- Verify repeated failures preserve the last known working target and that
  incompatible persisted-data migrations are blocked from reversible updates.
- Retained releases keep their environments; pruning cannot remove active or
  rollback resources. Settings, certificates, and readings survive.
- Test migration from the existing per-version environment layout using the
  previously installed script, not only clean installations.
- Run Windows lifecycle tests and a clean-machine installation smoke test;
  verify native BLE scanning on Windows and representative Pi hardware.
- Add focused tests in the existing test structure and CI platform matrix.
  Run targeted tests during development, then the documented hardware-free
  suite: `python -m pytest tests/ -q --ignore=tests/test_connection.py`.
- Verify LF endings and Bash syntax whenever changing `.sh` scripts.

## Decisions requiring confirmation during contract definition

- Exact supported Pi architectures/OS versions and Python versions.
- Whether Windows must bundle Python or may require a supported installation.
- Windows service mechanism and per-user versus machine-wide installation.
- Notification placement/default checking policy and retained-release policy.
- Data-migration compatibility guarantees across rollback versions.

## Status

Planning only. No repository files changed and implementation has not started.
