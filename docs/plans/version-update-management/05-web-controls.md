# 05 - Admin update controls

Status: defer until release notifications and the relevant platform
update/installer flow work reliably. Add controls only for a platform that
has a tested update path.

## Scope

- Let an authenticated admin start an update or revert and confirm the target
  release. Do not accept arbitrary URLs, paths or shell commands.
- Show basic progress and the final result. If installation fails, report
  whether the old app was restored; never claim success when recovery failed.
- Have the platform update command/installer verify release signatures,
  artifact hashes and compatibility itself. The web route is not a second
  updater and must not bypass those checks.
- Keep development checkouts and unsupported installs notification-only.

## Acceptance

- Test admin authorization, CSRF, confirmation and rejection of invalid
  releases.
- Test successful update, failed update, revert and failed recovery on each
  platform before exposing its controls.
- Update the relevant web and installation documentation.
