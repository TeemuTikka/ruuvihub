# 04 - Windows installer and updates

Status: in progress. A build helper creates the Windows x64 app payload
using an embeddable Python ZIP and locked wheels. Release tooling can sign,
verify and stage the Windows installer artifact. The launcher binds each
release to its own bundled Python and keeps data outside the payload.
The installer, updater, and choice of service or user-session mode remain.
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
  Setup installer is always available to download and install; a command-line
  Windows updater handles later updates and invokes the installer in its
  controlled update mode. Do not duplicate the Pi updater or share its
  transaction implementation.
- Test BLE scanning in the background service first. If it does not work,
  use the whole app in the logged-in user session; scanning pauses at sign-out.
  Choose and clearly identify one mode during setup. Do not add a separate
  BLE helper process or switch modes silently.
- Use the Windows updater to download and stage a new version, then
  activate it only after verification and preparation. Put the app and bundled
  Python in each versioned release directory. With the app stopped, select one
  directory by changing its versioned startup command in the Windows service
  configuration, or in the user-session startup task if that mode is selected.
  The command must reference the same release directory for both Python and
  app code, and selection must be one configuration change rather than two
  independent path updates. Start and health-check the selected version;
  restore the previous startup command if the check fails. Keep the prior
  version for revert.
  Preserve the database, settings, certificates and logs across install,
  update and uninstall; explicitly ask before removing persistent data.
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
  recoverable if preparation or startup fails. Test interruption before and
  after changing the startup command, including restart after a power loss;
  code and bundled Python must always come from the same versioned directory.
- Test signature/hash rejection before activation and ensure a failed update
  does not alter current app files or persistent data.
- Use the same previous-release database/settings compatibility check as the
  Pi release before shipping a Windows update that changes persisted data.
- Verify BLE under the service account. If it fails, verify the selected
  user-session mode while logged in and document its sign-out behavior.
- Update Windows, developer, general and release documentation.
