# 02 - Release notification service

Status: not started. Depends on the version and notification decisions in
[00](00-contracts.md); installation is explicitly outside this phase.

## Scope

- Add a bounded background checker against the public GitHub Releases API:
  startup and approximately daily checks with jitter, timeouts, conditional
  requests, bounded retry/backoff, rate-limit and offline handling. No network
  call may block app startup, BLE scanning, upload or backfill. Ensure task
  shutdown and cancellation are handled.
- Compare stable releases with `packaging.version.Version` after separately
  validating the release tag format. Ignore drafts, prereleases and malformed
  releases; avoid accidental downgrades. Define behavior when latest release
  metadata is missing, incomplete, withdrawn or not yet published.
- Keep latest known release data distinct from last attempt/status and its
  timestamp. A failed refresh must not present stale information as fresh.
  Bound response size and validate external release-note URLs before rendering.
- Persist an automatic-check setting enabled by default, with an authenticated, CSRF-protected
  manual Check now independent of the automatic setting. Prevent concurrent
  checks from multiplying requests. Show an admin-only update banner and
  details with Check now in System settings. Show signed-in admins current/latest
  versions, release notes, check status/time and compatibility when it can be
  established; otherwise say unknown rather than claim compatibility.
- `dev` checkouts show release information but never self-install. No
  installation control is introduced in this phase. Keep behavior available
  if platform metadata or the updater is absent.

## Acceptance

- Test numeric ordering, strict tags, `dev`, prereleases, drafts, incomplete
  and malformed responses, HTTP caching/304, timeouts, offline and rate limits,
  backoff/jitter with a fake clock, manual/disabled checks and duplicate
  requests. Ensure old data remains labeled stale after failure.
- Test authentication/CSRF, safe link rendering, settings persistence,
  independent task lifecycle and that network failure cannot delay or stop
  scanning, uploads, backfill or startup.
- Check relevant settings and developer guides and update the documentation
  for user-visible behavior and defaults.
