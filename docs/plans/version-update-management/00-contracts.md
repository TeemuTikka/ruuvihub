# 00 - Scope and basic contracts

Status: planning in progress. This is a personal-use update system in a public
repository, not a fleet product. Prefer the simplest maintainable design for
the owner's installations. Keep publisher verification, data preservation and
basic recovery; avoid broad support matrices and generalized frameworks.

## Confirmed decisions

- Pi: Raspberry Pi 3 Model B+ and Pi 5, 64-bit Raspberry Pi OS only. Use the
  latest stable Raspberry Pi OS and its system Python; document versions
  actually used instead of maintaining a compatibility matrix.
- Windows: 64-bit Windows 11, Inno Setup installer and bundled Python.
  Windows remains in scope. Test scanning in the background service; if BLE
  does not work there, use the whole app in the logged-in user session and
  clearly state that scanning pauses at sign-out. Windows 10 is deferred.
- Check GitHub Releases automatically and manually; checks must not interrupt
  sensor collection. Development checkouts are notification-only.
- Approved release source:
  [RuuviHub GitHub Releases](https://github.com/TeemuTikka/ruuvihub/releases).
  Installation and updates are online-only. The first downloaded bootstrap
  relies on HTTPS certificate validation and embeds the release public key.
  Signed metadata authenticates artifact hashes for later installs/updates.
- Keep the release-signing private key in a protected GitHub Actions secret.
  Limit the verifier-selection spike to one day. Choose a small established
  solution; include expiry/replay protection only if it is built in and
  straightforward, rather than adding custom update infrastructure. Planned
  rotation uses the current key; a compromised key requires local operator
  recovery. Do not require a separate paid Authenticode certificate for the
  Windows installer initially. Release-metadata signing and Windows installer
  code signing are different.
- Use `pyproject.toml` and one committed `uv.lock`; generate hash-verified pip
  deployment requirements. Do not add a second lock workflow or Pi dependency
  bundles.
- Keep application releases separate from settings, database/readings,
  certificates and logs. Back up persistent data before updating, but never
  automatically restore an older backup over newer readings. Roll back the
  application only when it can safely use the current data; otherwise stop
  and require operator recovery.
- Keep one previous working application version for revert. No configurable
  retention policy or fleet management is needed.
- First implement and validate the Pi path. Windows remains in scope, with
  a downloadable first-install installer and separate small Windows updater.
  Share release metadata/signature checks, not transaction machinery.

## Small set of implementation decisions

- Define only the release metadata needed to select the current release,
  platform artifact, version, minimum updater version and artifact hash.
  Use the selected signing verifier to authenticate it. Reject invalid
  signatures, mismatched hashes and artifacts not hosted by the approved
  GitHub release.
- Keep the Pi updater as a small operator command that downloads, verifies,
  stages and activates a versioned release; retain the previous release for
  manual or failed-start recovery. Add only the interruption handling needed
  to avoid leaving the installation unusable.
- Use a one-time online operator bootstrap to migrate an existing Pi install.
  It preserves the current app and persistent data, installs the updater, and
  verifies the existing app still starts. Do not make old scripts understand
  the new updater.
- For Windows, use Inno Setup for first install; the separate Windows updater
  invokes it in controlled update mode. Keep app versions separate so an
  update does not overwrite the last working version. First test
  service-account BLE; use the explicit user-session mode if needed.
- Add web update controls only after the relevant command/installer path works.
  Require an admin action and recheck version/signature before installation.

## Out of scope

- Fleet administration, exhaustive platform matrices, multiple installers,
  pluggable adapters, a persistent updater daemon, generic migration engines,
  configurable release retention and unattended updates.
- Offline install/update packages, local archive sideloading, Pi dependency
  bundles, Windows 10 support and automatic restoration of old database
  backups over newer data.

## Completion checks

- Document the actual Pi OS/Python and Windows mode tested.
- Verify update failure does not corrupt the active app or persistent data;
  verify the previous app can be restored when compatible with current data.
- Review relevant READMEs and run focused tests plus the documented
  hardware-free suite. Test BLE on available hardware when practical.
