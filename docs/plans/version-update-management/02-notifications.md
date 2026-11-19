# 02 - Release notifications

Status: not started. Can be implemented independently of installation using
the version and source decisions in [00](00-contracts.md).

## Scope

- Check the public GitHub Releases API at startup and periodically, without
  blocking app startup, BLE scanning, uploads or backfill. Include a manual
  admin Check now action. A connectivity failure is shown as a check error
  and does not affect the running app.
- Show the current and latest stable version, release notes link, and last
  check result in the admin UI. Ignore drafts and prereleases. Development
  checkouts may see notifications but never install updates.
- Keep the implementation small: use a timeout, avoid overlapping checks,
  and display stale/failed status honestly. Do not build a general retry,
  scheduling or release-channel framework.
- Defer web install/update controls to [05](05-web-controls.md), after the
  corresponding platform update path works.

## Acceptance

- Test newer/current versions, drafts and prereleases, unavailable GitHub,
  manual check and that network failure does not delay app startup or scanning.
- Verify only admins see the notification and check action.
- Update relevant settings and developer documentation.
