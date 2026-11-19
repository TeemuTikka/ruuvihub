# 03 - Pi updater and existing-install bootstrap

Status: not started. Depends on the basic release and dependency decisions in
[00](00-contracts.md) and [01](01-metadata-and-pi.md). Implement and validate
the Pi workflow first; this is not a shared cross-platform updater engine.

## Pi updater

- Provide a small operator command to check status, download the latest
  compatible release, install it, and revert to the previous working app.
  Web controls can call this command after it is reliable.
- Fetch only from
  [RuuviHub GitHub Releases](https://github.com/TeemuTikka/ruuvihub/releases).
  Verify signed metadata using the embedded public key, then verify the
  downloaded artifact's hash and size before extraction or activation. Reject
  invalid signatures, mismatched hashes, unexpected sources and unsafe archive
  paths. Updates require network access; local revert to the retained version
  does not.
- Download and prepare the new release without changing the active app.
  Keep the database, settings, certificates and logs in their persistent
  locations. Switch to the new version only after preparation; retain the
  previous version. Use the simplest activation/recovery mechanism compatible
  with the existing Pi layout. Handle interruption at download, preparation,
  switch and service restart without losing both working versions.
- Install locked dependencies from the configured package index when runtime
  dependencies change. Reuse the current environment for code-only releases
  when its lock identity matches. Do not build target-specific wheel bundles.
- Before updating, make a consistent data backup for operator recovery.
  Never replace current readings with that backup automatically. If the
  previous app cannot safely use current data, report that revert is blocked
  and require operator recovery.
- Use the same command for local operator updates and any later web-triggered
  update; do not add a daemon, plugin system, configurable retention policy,
  or general transaction framework. Keep one previous app version.

## Existing Pi installations

Provide a one-time online bootstrap, explicitly run by the operator:

1. Require that the current legacy updater is not running. Preserve the active
   app, venv, service configuration and persistent data.
2. Download the bootstrap from the approved release source over validated
   HTTPS. The bootstrap includes the release public key; verify signed
   metadata and artifact hashes before running downloaded application code.
3. Install the new updater and management entry points without overwriting an
   executing script. Keep the existing app selected during the handoff.
4. Verify the app still starts and the new update/revert command works. If
   handoff fails, leave or restore the legacy launch configuration.
5. Perform the first app update separately after bootstrap succeeds.

Do not extend the legacy updater to understand the new release format.

## Acceptance

- Test a clean install, an existing install bootstrap, code-only update,
  dependency-changing update, interrupted switch/start and revert.
- Verify bad signatures, hashes, sources and archives are rejected before
  activation, while a failed preparation leaves the active app and data
  untouched.
- Smoke-test on the selected 64-bit Raspberry Pi OS when hardware is
  available; document the actual OS/Python version used.
- Update Pi, developer, general and release documentation as applicable.
