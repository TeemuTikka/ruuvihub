# RuuviHub release and update plans

Status: reviewed planning only; implementation has not started. The split
plans below record the current decisions and are temporary working documents
on the feature branch; they will be removed before the pull request.

## Plan map

| Plan | Deliverable | Prerequisites |
|---|---|---|
| [00 - Contracts and decisions](00-contracts.md) | Supported targets, release, safety and lifecycle contracts; explicit bootstrap design | None |
| [01 - Metadata and Pi environment reuse](01-metadata-and-pi.md) | uv lock/export, online dependency installation and environment preparation | Relevant decisions in 00 |
| [02 - Release notifications](02-notifications.md) | Non-blocking, admin-visible release checks, including dev checkouts | Version/checking decisions in 00; artifact metadata from 01 only for artifact-specific compatibility |
| [03 - External updater and Pi integration](03-updater-and-pi.md) | Durable, recoverable engine and independently managed Pi lifecycle | 00 and 01; not dependent on 02 |
| [04 - Windows production lifecycle](04-windows.md) | Packaged install, update and rollback on Windows | 00 and shared engine contract from 03 |
| [05 - Web update controls](05-web-controls.md) | Explicitly approved update/revert controls | 02, 03 and/or 04 per platform |

Implement and validate each phase as a separately reviewable change. Phase
02 can run in parallel with 01 once its own contracts are settled. Phase 05
must expose controls only for a platform whose adapter is actually ready.
Do not treat this plan as authorization to choose unresolved user-facing
policies or to publish unsupported targets.

## Simplified implementation direction

- Deliver notifications independently first. Run focused feasibility checks
  for Windows service-account BLE and Pi package-index dependency installation
  before committing to their packaging implementation.
- Build one external command-line updater, not a persistent updater daemon,
  plugin framework or general-purpose installer. Share release validation,
  preparation and transaction logic; keep platform-specific operations small.
  Platform service management launches the command and recovery independently
  of the web process. A CLI alone does not provide authorization or reboot
  recovery: those remain explicit platform integration requirements.
- Use a documented one-time operator bootstrap for existing Pi installations.
  Do not make old scripts understand new metadata or coordinate two update
  engines. After successful handoff, installed management entry points use
  the new engine; retained legacy scripts are not supported active entry
  points. Preserve the old working application/environment and local recovery.
  The current released archive-install/update flow remains unchanged until
  the online bootstrap and updater are shipped; this plan does not itself
  remove functionality from existing installations.
- Start with a persisted-data compatibility generation for settings and the
  database, backed by forward/rollback tests. Do not build a release-pair
  compatibility graph or automatic migration planner. Known legacy transitions
  require a small reviewed mapping; incompatible generations require a separate
  operator procedure.
- Use an established signing format and verification implementation, not
  custom cryptography. Choose the smallest trust design meeting the confirmed
  signing, rotation and compromise-recovery requirements.
- Execute plan 01 in separate milestones: uv lock/export, signed release
  metadata, online package-index installation, then immutable environment
  preparation. Wire activation and existing-install migration once, in plan
  03, rather than implementing a temporary second update engine.
- Deliver a real Windows installer as part of plan 04. It installs and
  configures the machine-wide service and persistent data permissions; a
  release archive alone is not a production installation experience.
- First install uses a downloaded online bootstrap from
  [RuuviHub GitHub Releases](https://github.com/TeemuTikka/ruuvihub/releases).
  HTTPS certificate validation is the initial trust anchor; the bootstrap
  contains the release public key used to verify signed metadata and checks
  application artifacts against its authenticated hashes. This is not an
  offline install path.

Delivery sequence: notifications and feasibility checks; plan 01 milestones;
Pi command-line updater and bootstrap; Windows installer and lifecycle; web
controls per validated platform. No support targets or agreed safety
guarantees are removed.

## Implementation checks

- The current Windows development environment has Docker available. Use a
  Linux container for suitable Linux/Pi build and packaging checks, but do
  not treat container results as proof of Raspberry Pi hardware, BLE or
  systemd behavior. Run those checks directly on a Raspberry Pi later when
  needed.
- Run focused tests, then the hardware-free suite
  `python -m pytest tests/ -q --ignore=tests/test_connection.py`. Report
  missing platform or hardware evidence as a release blocker.
- Review and update relevant READMEs and release documentation with each
  implementation change. Preserve source-checkout/F5 workflows.
- For changed shell scripts, verify LF endings and run `bash -n`.
- Record unresolved decisions and actual platform coverage in the applicable
  phase plan. No application code has been changed here.
