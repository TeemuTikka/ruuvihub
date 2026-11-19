# 04 - Windows installer and updates

Status: not started. Windows remains in scope after the Pi updater foundation.
Use Windows-specific installation/update behavior; share the release metadata
and signature checks, not a cross-platform transaction engine. Development
checkouts remain notification-only.

## Scope

- Initially support 64-bit Windows 11. Use Inno Setup and bundle the Python
  runtime. Install the app and persistent data in separate locations; keep
  the previous application version so an update does not overwrite it.
- The operator downloads the installer from
  [RuuviHub GitHub Releases](https://github.com/TeemuTikka/ruuvihub/releases).
  Installation and updates are online-only. Verify signed release metadata
  and artifact hashes using the public key embedded in the installer.
- Keep first install and later updates as separate entry points: the Inno
  Setup installer is always available to download and install; a small
  Windows updater handles later updates and invokes the installer in its
  controlled update mode. Do not duplicate the Pi updater or share its
  transaction implementation.
- Test BLE scanning in the background service first. If it does not work,
  use the whole app in the logged-in user session; scanning pauses at sign-out.
  Choose and clearly identify one mode during setup. Do not add a separate
  BLE helper process or switch modes silently.
- Use the Windows updater to download and stage a new version, then
  activate it only after verification and preparation. Keep the prior version
  for revert. Preserve the database, settings, certificates and logs across
  install, update and uninstall; explicitly ask before removing persistent data.
- Release metadata signing authenticates app artifacts. Authenticode signing
  of the Windows installer is separate and is not required initially, to avoid
  making a paid certificate a prerequisite. Document any Windows warning
  users may see.
- Keep Windows-specific service, installer and activation code straightforward.
  Do not implement the Pi updater's internals a second time as a shared
  framework or add a permanent updater daemon.

## Acceptance

- On a clean Windows 11 machine, install, start RuuviHub, update, revert and
  uninstall. Verify persistent data survives and the previous app remains
  recoverable if preparation or startup fails.
- Test signature/hash rejection before activation and ensure a failed update
  does not alter current app files or persistent data.
- Verify BLE under the service account. If it fails, verify the selected
  user-session mode while logged in and document its sign-out behavior.
- Update Windows, developer, general and release documentation.
