# 03 - External updater and Pi integration

Status: not started. Depends on [00](00-contracts.md) and
[01](01-metadata-and-pi.md). Notifications are not a prerequisite.

## Scope

- Implement one external command-line engine with bounded actions and
  structured status, not a daemon or general-purpose plugin framework. Use an
  independently runnable runtime, durable state outside release directories,
  and a per-installation lock. Reuse plan 01's environment preparation; keep
  shared validation and transaction handling in the core. Limit platform
  operations to preparation specifics, service stop/start, activation/restore
  and health checking; no dynamic adapter discovery or extension registry.
- Use platform service management to launch the command independently of the
  web process and to run recovery after interruption/reboot. Define a narrow,
  authorized web launch boundary, not arbitrary privileged command execution.
  Route local update/revert commands through the same core and lock. A broken
  release or application venv must not prevent recovery.
- Keep initial updater upgrades an explicit operator maintenance action,
  separately validated with a retained working updater and recovery procedure.
  Reject releases requiring a newer updater until that action is complete;
  do not implement recursive updater self-update in the application transaction.
- Enforce the agreed trust boundary: the unprivileged app submits only a
  restricted action and release identity; the privileged side independently
  validates allowed source, manifest, compatibility, ownership and paths.
  Privileged execution must not import or execute app-user-writable code.
  On Pi, do not assume currently service-user-owned release or venv files are
  trustworthy to execute as root. Account for symlink/TOCTOU attacks and
  safe directory ownership at every privileged operation.
- Require authenticated release metadata with verified signatures for every
  installation and update; authenticate artifact identities, sizes and hashes
  against it. Reject normal updates to older or same versions. Define key
  provisioning, protected CI signing, rotation, revocation, compromise recovery
  and freshness per [00](00-contracts.md); do not rely on TLS or a checksum
  alone as publisher authentication.
- Fetch release metadata only from
  [RuuviHub GitHub Releases](https://github.com/TeemuTikka/ruuvihub/releases)
  and its approved asset hosts. The downloaded first-install bootstrap relies
  on HTTPS certificate validation to GitHub and contains the pinned release
  public key. It verifies signed release metadata with that key and checks
  artifact sizes and hashes against the authenticated metadata. HTTPS remains
  the initial trust anchor for obtaining the bootstrap itself.
- Stream downloads with verified TLS, bounded size/time and hash verification.
  Safely extract, rejecting traversal, escaping links, special files, archive
  bombs and duplicate/path-collision ambiguity. Verify disk space and target
  and updater compatibility before activation. All bootstrap, update and
  dependency preparation requires network access to the approved GitHub
  Releases source; reject local archives and sideloaded bundles. Do not offer an
  unsigned fallback when signature verification fails. Explain that hashes
  alone are not publisher authentication.
- Stage application and immutable environment while collection continues.
  Preflight the compatibility generation and supporting evidence specified in
  plan 00; do not construct a release-pair compatibility engine. Perform any
  required consistent backup. Journal
  the last known working code, environment and launch configuration before
  stopping, switching and restarting. Verify service/process and expected
  `/healthz` version; on failure restore and independently health-check the
  previous version. Retain diagnostics and distinguish rollback failure.
- Specify crash-safe ordering, atomic replacement and durable flushes for
  journal and activation changes; recover idempotently at each boundary,
  including the existing two-link switch and after restart/reboot. Treat
  ambiguous state as recovery-required rather than success.
- Expose a command-line update action that fetches signed artifacts online,
  plus status and explicit local revert commands, including when the dashboard
  is down. Keep the previous working target until replaced by
  a later successful activation; repeated failures do not replace it.
  Retain three successful versions by default with an operator override.
  Bound failed staging/diagnostic retention after later success or explicit
  cleanup; prune only resources unreferenced by active, pending, rollback or
  recovery-required state.
- Integrate new installs through online signed bootstrap using the new engine.
  Migrate existing Pi installations with the one-time online bootstrap below.
  Do not retain local archive installation or a second active update engine.
  Local rollback to a retained release remains available without network
  access.

## One-time operator bootstrap

1. Require explicit operator invocation and no in-flight legacy management
   operation. The old scripts do not honor the new lock; do not claim the lock
   prevents an independently launched legacy command. Document this exclusive
   maintenance window and fail on detected conflicts.
2. Inspect the actual installation, service configuration and legacy venv
   layout. Record the working release/environment and launch configuration;
   preserve shared settings, readings, logs, certs and custom paths.
3. Retrieve bootstrap metadata and artifacts from the approved GitHub
   Releases source. The downloaded bootstrap relies on HTTPS certificate
   validation as its initial trust anchor and contains the pinned release
   public key; use it to verify signed metadata and check artifacts against
   the signed hashes before privileged execution. Never execute bootstrap code
   merely because it came from an application-writable release directory.
4. Durably record the handoff and atomically replace installed management
   entry points with thin new-engine wrappers. Install service/recovery wiring
   without overwriting an executing script in place. Retain the old launch
   configuration for restoration, not as a concurrently supported updater.
5. Verify the current application still runs and local status/recovery works.
   Mark bootstrap complete only after verification. Interrupted bootstrap must
   resume or restore the pre-handoff setup; it must not report success early.

Bootstrap establishes management state, not a simultaneous application
upgrade. Perform the first application update as a separate normal transaction
after bootstrap verification. Preserve rollback to the recorded legacy
application where compatibility has been reviewed.

## Acceptance

- Failure injection at every journal/activation boundary, including process
  kill, reboot, mixed release/venv pointers, stopped service and recovery
  restarted repeatedly. Verify durable state, lock recovery and idempotence.
- Test unsafe archives, corrupted/oversized downloads, low space, incompatible
  metadata/data, concurrent local and remote requests, failed prepare/start,
  wrong health version, successful rollback and failed rollback. Confirm
  preparation failures leave collection running.
- Test unavailable network/API, missing, tampered, wrong-key, invalid,
  expired or revoked signatures,
  signed artifact substitution, key rotation/recovery and replay cases as
  defined by the trust contract. Verify there is no local-file or unsigned
  install path, and that update-service failures leave the active release
  running and local rollback usable.
- Test update, explicit revert after a superficially healthy update, and
  local recovery without the web process. Confirm failed attempts preserve
  the last known good resources and application data/credentials/certificates.
- Test malicious unprivileged request fields and writable path/symlink swaps;
  verify the updater cannot execute injected code or access arbitrary paths.
- Exercise online bootstrap from genuine old per-version and single-venv
  layouts, including interruption at each handoff boundary, restoration and
  conflicting legacy operation detection. Verify installed
  management entry points no longer invoke the old engine. Test the first
  normal update and rollback to the recorded legacy application separately. Run Pi
  systemd and representative hardware tests before advertising support.
- Update Pi, developer, general and release documentation as applicable.
