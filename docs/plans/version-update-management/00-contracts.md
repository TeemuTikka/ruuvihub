# 00 - Release, safety and platform contracts

Status: planning in progress. Decisions below reflect the interview through
Q24; unanswered questions remain open. No implementation is authorized by
these notes.

## Confirmed product decisions

- Preserve the currently documented Pi 3, Pi 4 and Zero W, Raspberry Pi OS
  Bullseye or newer, and Python 3.10+ support. A specific target must not be
  advertised as supported until its online release artifact and dependency
  installation from the configured package index is validated. Validation
  failures block support, not silently
  remove the existing commitment.
- The installed Windows app bundles its Python runtime and runs as a
  machine-wide service without requiring a signed-in user. Working native
  BLE under the selected service account is a support gate; do not silently
  fall back to a user-session app. Development checkouts remain notification
  only and retain the developer workflow.
- Check for releases automatically by default, with a persistent opt-out and
  a manual check. Show an admin-only availability banner and release details
  plus Check now under System settings. Online updating is the default.
- Verified release signatures are required for web installation. Use a
  protected CI-managed signing key with automatic signing. Planned key
  rotation requires authorization by a currently trusted key; compromise
  requires local operator recovery. Once the device knows its trusted key is
  revoked, block installation until local recovery. Devices learn of
  revocation only when they can contact a trusted release service.
- The approved release source is
  [RuuviHub GitHub Releases](https://github.com/TeemuTikka/ruuvihub/releases).
  First-install bootstrap and subsequent update requests use this repository's
  releases and assets only. The downloaded first-install bootstrap trusts
  GitHub through HTTPS certificate validation and contains the release public
  key used to verify signed metadata; artifact sizes and hashes are checked
  against that authenticated metadata. HTTPS is the initial trust anchor for
  that bootstrap; later release metadata must verify against the pinned key.
- All installation and update operations require network access to the
  approved release service to retrieve signed metadata and required artifacts.
  There is no manual archive installation, sideloaded dependency bundle, or
  unsigned-package exception. If connectivity is unavailable, an update is
  unavailable; the running application continues operating, and local
  rollback to a retained release remains available.
- Block reversible updates unless rollback compatibility can be established,
  including data and settings written after activation while Revert remains
  available. For legacy versions, require a reviewed compatibility rule
  covering the actual source and target; otherwise use an explicit
  operator-managed migration/recovery procedure. Never silently discard
  newer readings by restoring a database.
- Retain three successful versions by default, with operator override and
  protection for active and rollback targets. Failed staging and diagnostics
  have bounded cleanup after a later success or explicit operator cleanup;
  never prune resources needed for pending or recovery-required transactions.
- Normal web updates reject older and same-version releases. Explicit Revert
  restores a retained compatible target; other downgrades require an
  operator-managed procedure.

## Remaining decisions and design work

- Enumerate supported Pi models, 32/64-bit architectures, Raspberry Pi OS
  and Python versions, including an explicit policy for future OS/Python
  versions. Prove native dependency availability for every advertised
  combination. Any proposed narrowing requires explicit product approval
  and an operator migration path.
- Define Windows architecture/OS coverage, installer technology, service
  account and elevation model, data directory, ACLs and uninstall policy.
  Deliver an installer that configures the machine-wide service and persistent
  data permissions. Verify BLE under that account on each supported
  configuration.
- Specify the practical settings/database compatibility guarantees and
  user-visible handling of incompatible or skipped releases. Use a simple
  compatibility generation plus tests, not a general release-pair rules engine.
- Define trust assumptions: unprivileged application compromise, admin
  authorization, updater/service account, local operators, GitHub Releases
  and GitHub's HTTPS certificate chain for initial bootstrap, network and
  filesystem. Unattended installation is outside initial scope.
- Specify online artifact selection from the approved GitHub repository,
  provenance, network-failure behavior and manifest verification. Restrict
  release metadata and artifact URLs to that source and expected GitHub asset
  hosts. Do not provide a local-file or unsigned fallback, or bypass checks
  because a signature check failed.
- Choose signing technology and define canonical signed content, root-key
  provisioning, CI key permissions, compromise response, revocation
  distribution, expiry/freshness, replay and rollback protection, and
  rotation. A signed version number alone cannot prove that metadata is fresh.
  Assess CI-key theft and release-workflow compromise explicitly.
  Reuse an established signing format and verifier; do not design custom
  cryptography or a broad update-metadata framework without demonstrated need.

## Contracts to specify

1. Versioned manifest schema: stable tag grammar and version comparison,
   source commit, platform/architecture/package/runtime selectors, artifact
   size and digest, application version, updater compatibility, persisted
   data compatibility, release status and schema evolution. Define
   missing/unknown fields and fail-closed behavior, redirect and URL
   restrictions, GitHub rate-limit/connectivity behavior, and asset publication
   completeness. Specify whether older clients can read new manifests.
   Bind the application artifact, compatibility claims and hashes to
   authenticated release metadata. Define signature verification and release
   completeness before any web installation.
2. Build lock/export policy: `pyproject.toml` is project metadata and
   `uv.lock` is the single dependency lock. Generate exact, hash-verified pip
   deployment requirements from that lock. Do not maintain a second lock
   workflow or independently edited exports. Define runtime versus development
   inputs, target markers, selected interpreter and installer policy. Prove
   uv resolution/export and pip installation with native dependencies on the
   agreed matrix before rollout. Reproducibility does not imply artifact
   authenticity.
3. Environment identity and readiness: canonical effective runtime
   dependencies and hashes, interpreter implementation/version/ABI,
   platform/architecture and installer policy, excluding application
   version and unrelated dev-only dependencies. Define how to validate
   a ready environment and detect a missing or changed base interpreter.
   No in-place mutation or relocation of referenced venvs.
4. Rollback eligibility: assign a persisted-data compatibility generation
   covering settings and database behavior. Reversible releases must preserve
   bidirectional compatibility within that generation, including subsequent
   writes and skipped releases, demonstrated by tests against supported
   rollback targets. A matching identifier alone is not proof. Handle known
   legacy sources with a small reviewed mapping to a generation; do not build
   a general compatibility graph. Block incompatible or unproven transitions
   and provide an operator-managed procedure. Never restore an older database
   silently or discard readings.
5. External updater command contract: one CLI with bounded actions and
   structured status, one lock and one durable transaction record per install.
   The updater independently resolves allowed source and metadata. Define the
   platform-managed launch/authorization boundary, request replay/duplicate
   handling, permissions, path ownership, recovery and logging. Use the same
   core for local and web requests; no persistent daemon or pluggable protocol
   framework initially. Never execute app-user-writable code with privilege.
   Keep the updater runnable independently; handle its own initial upgrades
   through an explicit operator maintenance procedure rather than recursive
   self-updating machinery.
6. One-time operator bootstrap for existing installs: inspect the actual
   legacy layout and running selection, validate trusted bootstrap inputs,
   preserve the previous code/environment and launch configuration, then
   install and hand off to new management components. Do not invoke the old
   updater to install its replacement or extend old scripts with the new
   protocol. Define an interruption-safe handoff, restoration before handoff
   completes, and local recovery afterwards. Replace installed management
   entry points with new-engine wrappers; do not support simultaneous legacy
   and new mutation paths.

## Exit criteria

- Decisions and exact target matrix recorded; unresolved issues explicitly
  block the affected platform or action, not notifications generally.
- A worked transition covers an existing per-version venv install, a
  pre-per-version legacy install, a new installation and a skipped release.
  Exercise failed/interrupted bootstrap using genuine
  legacy install layouts and verify the recorded old launch configuration
  remains recoverable without relying on the new application.
- Threat-boundary review demonstrates the proposed privileged updater
  cannot be steered through app-writable metadata, symlinks, archive paths,
  scripts, environment variables or arbitrary commands. If not demonstrable,
  revise the boundary before implementing web-triggered updates.
- Record which target tests require real hardware or clean machines and
  where they will be run; no unsupported artifact is marked installable.
