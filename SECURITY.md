# Security policy

## Report a vulnerability privately

Use [GitHub private vulnerability reporting](https://github.com/TeemuTikka/ruuvihub/security/advisories/new):
open **Security → Advisories → Report a vulnerability** in this repository.
Do not disclose vulnerability details in public issues, pull requests, or comments.

Private vulnerability reporting must be enabled when the repository becomes
public, before announcing the first public release. Until the reporting form is
available, this reporting route is not active; do not substitute a public
issue for a private report.

Please include:

- The affected RuuviHub version and operating system.
- Steps to reproduce, preferably with synthetic sensor data and a minimal
  example or proof of concept.
- The expected and observed behavior, potential impact, and any known workaround.

Remove credentials, session cookies, private keys, and private sensor data
from attachments and logs. Discuss disclosure timing within the private report.

## Deployment assumptions

RuuviHub is intended for a trusted home or local network. Anyone who can
reach an instance can view its dashboard, sensor history, and CSV exports
without signing in. Hiding a sensor is a display preference, not access control.
Admin login protects settings and the live log stream.

Change the default admin password before giving others network access,
and avoid exposing the service directly to the internet. See
[README.md](README.md) for the documented authentication and HTTPS behavior.
