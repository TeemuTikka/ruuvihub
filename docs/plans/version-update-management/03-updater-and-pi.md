# 03 - Pi updater and existing-install bootstrap

Status: in progress. Verified release download and staging are implemented
in `scripts/pi_release.py`. Atomic selection and revert are implemented
in `scripts/pi_activation.py`. The operator command now combines these
helpers with systemd checks and pre-update SQLite/settings backups.
Bootstrap, pruning, and persisted-data compatibility checks remain.
Depends on [00](00-contracts.md) and [01](01-metadata-and-pi.md).

## Pi updater

- Provide a small operator command to check status, download the latest
  compatible release, install it, and revert to the previous working app.
  Updates and reverts are started from the command line.
- Fetch only from
  [RuuviHub GitHub Releases](https://github.com/TeemuTikka/ruuvihub/releases).
  Verify signed metadata using the embedded public key, then verify the
  downloaded artifact's hash and size before extraction or activation. Reject
  invalid signatures, mismatched hashes, unexpected sources and unsafe archive
  paths. Updates require network access; local revert to the retained version
  does not.
- Download and prepare the new release without changing the active app.
  Keep the database, settings, certificates and logs in their persistent
  locations. Each `releases/<version>/` directory contains the app and a
  `venv` link to its prepared environment under `shared/venvs/`. Change the
  systemd `ExecStart` to use absolute paths under `current/venv/bin/python3`
  and `current/src/main.py`, so `current` selects code and dependencies
  together. After preparation, stop the service, take a consistent data
  backup, and atomically replace `current` using a temporary link and rename in the
  same directory. Restart and check service status plus the expected `/healthz`
  version. On a failed check, atomically point `current` back to the retained
  release and restart it.
  A power loss during replacement leaves `current` pointing to either complete
  release. If the selected release fails after reboot, the operator can revert
  from the command line. Keep the previous release and every environment it
  references.
- Install locked dependencies from the configured package index when runtime
  dependencies change. Reuse an installed environment for code-only releases
  when its runtime lock and system Python identity match. Never modify an
  environment referenced by the active or previous release. Do not build
  target-specific wheel bundles.
- Before updating, make a consistent data backup for operator recovery.
  Never replace current readings with that backup automatically. Normal
  releases must preserve compatibility with the immediately previous app. If
  unexpected incompatibility is found during revert, stop and require
  operator recovery rather than restoring an older backup over newer data.
- Use the same command for operator updates and reverts; do not add a daemon,
  plugin system, configurable retention policy or general transaction
  framework. Keep one previous app version.

## Existing Pi installations

Provide a one-time online bootstrap, explicitly run by the operator:

1. Require that the current legacy updater is not running. Preserve the active
   app, venv, service configuration and persistent data.
2. Download the bootstrap from the approved release source over validated
   HTTPS. The bootstrap includes the release public key; verify signed
   metadata and artifact hashes before running downloaded application code.
3. Add a `venv` link inside the active legacy release pointing to the resolved
   target of its existing `shared/venv` (or that directory itself). Preserve
   that environment. Install the new updater and management entry points
   without overwriting an executing script. Keep the existing app selected.
4. Change the systemd command from `shared/venv` to `current/venv`, reload and
   restart the service, then check status and the expected `/healthz` version.
   Verify the new update/revert command works. If the handoff fails, restore
   the saved legacy service configuration and leave its app and venv intact.
5. Perform the first app update separately after bootstrap succeeds.

Do not extend the legacy updater to understand the new release format.

## Acceptance

- Test a clean install, an existing install bootstrap, code-only update,
  dependency-changing update, interrupted switch/start and revert. Simulate
  interruption before and after the single `current` replacement, including
  reboot/startup, and verify no code/environment mismatch is selected.
- For each release that changes the database or persisted settings, run the
  new release's migration and a representative write against disposable data,
  then start the immediately previous release on that copy and verify it can
  read and write. Make this a release CI check; an incompatible change must
  be staged across releases before publication.
- Verify bad signatures, hashes, sources and archives are rejected before
  activation, while a failed preparation leaves the active app and data
  untouched.
- Smoke-test on the selected 64-bit Raspberry Pi OS when hardware is
  available; document the actual OS/Python version used.
- Update Pi, developer, general and release documentation as applicable.
