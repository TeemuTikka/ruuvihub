# 03 - External updater and Pi integration

Status: not started. Depends on [00](00-contracts.md) and
[01](01-metadata-and-pi.md). Notifications are not a prerequisite.

## Scope

- Implement an external engine with a stable, versioned protocol and
  independently runnable runtime, durable state outside release directories,
  and a per-installation lock. Define its own bootstrap, upgrade and rollback
  so a broken release or venv cannot take down recovery. Route all new manual
  and web-triggered operations through the same serialization mechanism.
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
  Preflight data compatibility and any required consistent backup. Journal
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
- Migrate existing Pi scripts and service without a window in which old
  commands can race the new engine. If legacy commands cannot participate
  in the same lock safely, require an explicit operator bridge rather than
  claiming concurrent safety. Preserve offline manual archive workflow.

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
- Exercise the actual old-script-to-new-updater bridge, including failure
  during first transition and coexistence with legacy installs. Run Pi
  systemd and representative hardware tests before advertising support.
- Update Pi, developer, general and release documentation as applicable.
