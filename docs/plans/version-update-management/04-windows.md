# 04 - Windows production installation and lifecycle

Status: not started. Depends on the Windows decisions in
[00](00-contracts.md) and the shared engine contract in
[03](03-updater-and-pi.md). Windows development/F5 remains supported
without self-update.

## Scope

- Select and document a Windows-specific package for a machine-wide service;
  do not assume Pi tarballs, Bash, systemd, symlink privileges or a local
  Python installation. Use one application version while allowing different
  artifact kinds and runtime strategies. Preserve license notices.
- Define install/upgrade/uninstall flows, service account,
  elevation, writable persistent data location, ACLs, startup/restart,
  log access, recovery command and clean-machine prerequisites. Keep
  credentials, SQLite, logs and TLS material outside replaceable releases.
  Explicitly determine whether/how uninstall retains operator data.
- Build and validate target-specific artifacts in Windows CI from the same
  reviewed dependency inputs. Bundle the Python runtime; keep prior bundle
  and runtime independently restorable. Do not mutate locked running files.
  The shared engine performs transaction handling; the adapter manages
  Windows-specific preparation, activation, restart and restore.
- Integrate a least-privilege control boundary, restricted request protocol
  and independent recovery runtime. Gate installed-update capability on
  validated artifact, account/ACL and persisted-data compatibility. Source
  checkouts remain notification-only.
- Support a fully offline manually transferred update, including all runtime
  and dependencies, as well as the default online flow. Exact package
  boundaries must account for the bundled runtime and are not assumed to be
  identical to Pi's application/dependency bundles.

## Acceptance

- Test clean install, normal update, failed preparation, failed restart,
  wrong health version, automatic restoration, explicit manual revert,
  repeated failure, interrupted activation/recovery, local rollback without
  UI, pruning and uninstall/data retention.
- Verify persisted data and certs survive; prove the restored bundle/runtime
  works after a failed update and that no running-file or symlink assumptions
  break activation. Validate ACL and request-boundary attacks.
- Run CI lifecycle tests, a clean-machine installation smoke test, a
  disconnected update test and representative real Windows BLE scanning
  under the chosen service account. Failure to establish reliable BLE blocks
  support; do not fall back silently to a user-session app.
- Update Windows, developer, general and release documentation for actual
  installation, lifecycle and recovery behavior.
