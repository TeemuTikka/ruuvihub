# 04 - Windows production installation and lifecycle

Status: not started. Depends on the Windows decisions in
[00](00-contracts.md) and the shared engine contract in
[03](03-updater-and-pi.md). Windows development/F5 remains supported
without self-update.

## Scope

- Select and document a Windows installer and package for a machine-wide service;
  do not assume Pi tarballs, Bash, systemd, symlink privileges or a local
  Python installation. Use one application version while allowing different
  artifact kinds and runtime strategies. Preserve license notices.
- Build a production installer for clean install, service registration and
  configuration, persistent data directory and ACL setup, upgrades, and
  uninstall. Define service account,
  elevation, writable persistent data location, ACLs, startup/restart,
  log access, recovery command and clean-machine prerequisites. Keep
  credentials, SQLite, logs and TLS material outside replaceable releases.
  Explicitly determine whether/how uninstall retains operator data.
- The operator downloads and runs the installer from
  [RuuviHub GitHub Releases](https://github.com/TeemuTikka/ruuvihub/releases).
  It uses HTTPS for initial retrieval and the embedded release public key to
  verify signed metadata and check application artifacts against its
  authenticated hashes; it is not an offline installer.
- Build and validate a signed Windows installer/update artifact in Windows CI
  from the same reviewed dependency inputs. Bundle the Python runtime; keep
  prior application/runtime independently restorable. Do not mutate locked
  running files. The shared command-line engine performs transaction handling;
  a small platform implementation manages Windows-specific preparation,
  activation, restart and restore.
- Integrate a least-privilege control boundary, restricted request protocol
  and independent recovery runtime. Gate installed-update capability on
  validated artifact, account/ACL and persisted-data compatibility. Source
  checkouts remain notification-only.
- Use Windows service management to launch update/recovery independently of
  the app, without introducing a second persistent updater daemon. Implement
  the narrow command/authorization boundary from plan 03, not a parallel
  Windows transaction engine or plugin framework.
- Require online retrieval of signed Windows release artifacts for install
  and update. Do not support sideloaded or offline update packages. Local
  rollback to a retained application/runtime bundle remains available without
  downloading another release.

The installer is the production install experience. Decide whether updates
invoke it in a controlled mode or activate a versioned installed bundle; an
in-place installer must not destroy the last-known-good runtime. This is a
packaging decision, not a reason to duplicate update transaction logic.

## Acceptance

- Test installer-driven clean install and uninstall, normal update, failed
  preparation, failed restart,
  wrong health version, automatic restoration, explicit manual revert,
  repeated failure, interrupted activation/recovery, local rollback without
  UI, pruning and uninstall/data retention.
- Verify persisted data and certs survive; prove the restored bundle/runtime
  works after a failed update and that no running-file or symlink assumptions
  break activation. Validate ACL and request-boundary attacks.
- Run CI lifecycle tests, a clean-machine installation smoke test, update
  service connectivity-failure tests and representative real Windows BLE scanning
  under the chosen service account. Failure to establish reliable BLE blocks
  support; do not fall back silently to a user-session app.
- Update Windows, developer, general and release documentation for actual
  installation, lifecycle and recovery behavior.
