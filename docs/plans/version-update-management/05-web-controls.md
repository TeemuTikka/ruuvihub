# 05 - Explicit web-approved update and revert

Status: not started. Requires [02](02-notifications.md) and a validated
updater/adapter from [03](03-updater-and-pi.md) and/or
[04](04-windows.md). Ship capability per platform, not a blanket claim.

## Scope

- Detect installed/update/revert capabilities from the independent updater.
  Offer controls only when the target artifact, updater, persistence
  compatibility, trust policy and recovery path satisfy the contracts.
  Unsupported and development installs remain notification-only.
- Require verified signed release metadata and artifact identity before web
  installation. Reject normal updates to older or same versions and block
  installation when the trusted key is known to be revoked. Do not provide
  an unsigned-package override through the web interface.
- Add signed-in admin, CSRF-protected update and revert actions. Display
  release identity, compatibility and update consequences before explicit
  confirmation; never submit arbitrary URLs, paths or shell commands.
  Independently revalidate on the updater side to handle stale UI state.
- Prevent duplicate/concurrent actions and expose durable progress: idle,
  downloading, preparing, activating, verifying, succeeded, rolled back,
  failed and recovery-required, including separately reported update and
  rollback outcomes. Distinguish stale status or unavailable updater.
- Handle disconnects during restart, reconnect and reload without losing the
  transaction. Show current version and last known working revert target.
  A successful startup check is not a guarantee against later defects.
- Keep automatic unattended installation, prerelease channels and force
  downgrade outside this initial scope.

## Acceptance

- Test authentication, CSRF, explicit confirmation, stale or incompatible
  target rejection, signature/revocation and downgrade gating, forged request
  fields, repeated clicks and concurrent
  clients. Confirm the updater repeats all security and compatibility checks.
- Simulate browser disconnect/reload, app restart, updater unavailability,
  preparation failure, successful activation, automatic rollback, manual
  revert and failed restoration. Ensure UI never reports failed restoration
  as success or implies collection continues during a service stop.
- End-to-end test each supported platform with real adapter/lock/journal;
  test development and unsupported targets never offer installation.
- Update general, platform and developer documentation with operation,
  recovery, checksums-versus-authentication limitations and offline behavior.
