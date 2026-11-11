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
- Require authenticated release metadata with verified signatures for all web
  installations; authenticate artifact identities, sizes and hashes against
  it. Reject normal web updates to older or same versions. Define key
  provisioning, protected CI signing, rotation, revocation, compromise recovery
  and freshness per [00](00-contracts.md); do not rely on TLS or a checksum
  alone as publisher authentication.
- Stream online downloads with verified TLS, bounded size/time and hash verification.
  Safely extract, rejecting traversal, escaping links, special files, archive
  bombs and duplicate/path-collision ambiguity. Verify disk space and target
  and updater compatibility before activation. State the checksum trust
  limitation in operator-facing documentation. For manual offline updates,
  accept separately transferred application and compatible dependency bundles
  without any network access. Allow an unsigned package only after explicit
  local command-line approval, warning and audit; no web bypass. Do not turn
  failed signature verification into a silent unsigned fallback.
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
- Expose local update, status and explicit revert commands, including when
  the dashboard is down. Keep the previous working target until replaced by
  a later successful activation; repeated failures do not replace it.
  Retain three successful versions by default with an operator override.
  Bound failed staging/diagnostic retention after later success or explicit
  cleanup; prune only resources unreferenced by active, pending, rollback or
  recovery-required state.
- Integrate new installs directly with the new engine. Migrate existing Pi
  installations with the one-time bootstrap below. Preserve offline manual
  archive updates through the CLI; do not retain a second active update engine.

## One-time operator bootstrap

1. Require explicit operator invocation and no in-flight legacy management
   operation. The old scripts do not honor the new lock; do not claim the lock
   prevents an independently launched legacy command. Document this exclusive
   maintenance window and fail on detected conflicts.
2. Inspect the actual installation, service configuration and legacy venv
   layout. Record the working release/environment and launch configuration;
   preserve shared settings, readings, logs, certs and custom paths.
3. Validate bootstrap provenance using operator-established trust before
   privileged execution, then prepare the independent updater and required
   offline inputs. Never execute bootstrap code merely because it came from
   an application-writable release directory.
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
- Test missing, tampered, wrong-key, invalid, expired or revoked signatures,
  signed artifact substitution, key rotation/recovery and replay cases as
  defined by the trust contract. Verify the local-only unsigned exception is
  explicit and audited, and cannot be invoked through the web boundary.
- Test fully disconnected dependency-changing Pi update using transferred
  application and compatible dependency bundle; verify no package index
  access is attempted and the prior version remains available for rollback.
- Test update, explicit revert after a superficially healthy update, and
  local recovery without the web process. Confirm failed attempts preserve
  the last known good resources and application data/credentials/certificates.
- Test malicious unprivileged request fields and writable path/symlink swaps;
  verify the updater cannot execute injected code or access arbitrary paths.
- Exercise bootstrap from genuine old per-version and single-venv layouts,
  including interruption at each handoff boundary, restoration, conflicting
  legacy operation detection and a fully offline run. Verify installed
  management entry points no longer invoke the old engine. Test the first
  normal update and rollback to the recorded legacy application separately. Run Pi
  systemd and representative hardware tests before advertising support.
- Update Pi, developer, general and release documentation as applicable.
