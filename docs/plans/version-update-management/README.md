# RuuviHub release and update plans

Status: planning only; implementation has not started. These temporary
working documents are on the feature branch and will be removed before the
pull request.

## Guiding constraint

RuuviHub is public, but this update system is primarily for the owner's
personal installations. Choose straightforward solutions that work for that
setup; do not build a fleet product or general-purpose updater framework.
Keep basic publisher verification, protect persistent data, and retain a
simple recovery path.

## Plan map

| Plan | Purpose |
|---|---|
| [00 - Scope and basic contracts](00-contracts.md) | Record selected targets and simple safety rules |
| [01 - Release metadata and Pi dependencies](01-metadata-and-pi.md) | Use one lock/export flow and prepare app dependencies |
| [02 - Release notifications](02-notifications.md) | Show current/latest stable release without blocking the app |
| [03 - Pi updater/bootstrap](03-updater-and-pi.md) | Implement the Pi updater and migrate existing installs |
| [04 - Windows installer/updates](04-windows.md) | Implement Windows 11 installation and update behavior |
| [05 - Web controls](05-web-controls.md) | Add admin-triggered actions after their platform paths work |

## Implementation direction

- Implement Pi first: Pi 3 Model B+ and Pi 5, 64-bit Raspberry Pi OS, latest
  stable release and its system Python. Document what was actually used;
  no broad compatibility matrix.
- Use `uv.lock` and generated hash-verified pip requirements. Do not publish
  Pi dependency bundles.
- Use the official
  [GitHub Releases](https://github.com/TeemuTikka/ruuvihub/releases) source
  for online first install and updates. The first bootstrap relies on HTTPS
  certificate validation and embeds the release public key. Signed metadata
  authenticates artifact hashes for later releases.
- Limit selection of a small established signing verifier to one day. Use
  built-in expiry/replay protection only if straightforward; do not add custom
  update infrastructure for it. Store the private key as a protected GitHub
  Actions secret; handle key compromise through local operator recovery.
  Release metadata signing is separate from Authenticode signing; a paid
  Windows code-signing certificate is not an initial requirement.
- Keep Pi updating small and operator-driven. Retain the previous app
  version, not a configurable release history. Keep the downloadable Inno
  Setup first installer available; use a separate small Windows updater for
  later updates, invoking Inno Setup in controlled update mode. Share release
  metadata and verification, not transaction machinery.
- Test Windows BLE under the service account. If it does not work, use the
  whole app in the logged-in user session; scanning pauses at sign-out. The
  live developer-session scan already works, but service-mode BLE remains
  unverified.
- Keep the database/settings outside app release directories. Back up before
  updating, but never automatically restore a backup over newer readings.
  Permit app rollback only when it can safely use current data.
- Add simple admin notifications. Add web update controls only after the
  matching command or installer works reliably.

## Validation

- Use Docker for suitable Linux builds and automated checks. Smoke-test on
  available Pi hardware when practical; containers cannot verify real BLE or
  systemd behavior.
- Run focused tests and the hardware-free suite:
  `python -m pytest tests/ -q --ignore=tests/test_connection.py`.
- Update relevant READMEs with implementation changes and document the
  actual OS/Python and Windows BLE mode tested.
