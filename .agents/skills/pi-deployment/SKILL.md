---
name: pi-deployment
description: "Use when changing or validating RuuviHub Raspberry Pi install, update, rollback, systemd deployment, release archives, or persistent install-time settings."
---

# Raspberry Pi deployment workflow

Use this workflow for changes to the existing Pi release and management
scripts. Read the relevant sections of `README-pi.md`,
`README-dev.md`, `docs/releases.md`, and `.github/copilot-instructions.md`
before editing. Do not operate on a real installation without explicit
authorization.

1. Trace the affected behavior through `scripts/install.sh`, `update.sh`,
   `rollback.sh`, `lib.sh`, the service template, the release builder and
   copied management-script lists as applicable. Preserve settings,
   database, logs, TLS material and operator-specified paths.
2. Check upgrade compatibility using the **previously installed** updater,
   not only the new source script. Decide whether a migration must run before
   startup or can run in the application, using the existing persisted-default
   migration guidance. Preserve atomic replacement of a running script.
3. Treat the active release and its Python environment as one rollback unit.
   Check service status and the expected `/healthz` version, not only an
   HTTP response. Examine activation, failure restoration and pruning together;
   never remove an active or needed rollback resource.
4. Review root/service-user boundaries and validate paths derived from
   archives or version strings. Do not assume shell extraction or checksums
   authenticate an archive.
5. Add focused tests for the changed behavior, including failure and legacy
   transition paths where relevant. `tests/test_release_tools.py` covers
   release-building behavior; it does not by itself prove systemd, rollback
   or real Pi behavior. Use isolated fixtures or a disposable environment,
   not a deployed service; report any untested transition or hardware case.
6. For every changed `.sh` file, verify LF endings and run `bash -n` where
   available. Run relevant tests and update affected deployment and release
   documentation. Report limitations if Bash, systemd or Pi hardware is
   unavailable.
