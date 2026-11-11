# 00 - Release, safety and platform contracts

Status: planning in progress. Decisions below reflect the interview through
Q24; unanswered questions remain open. No implementation is authorized by
these notes.

## Confirmed product decisions

- Preserve the currently documented Pi 3, Pi 4 and Zero W, Raspberry Pi OS
  Bullseye or newer, and Python 3.10+ support. A specific target must not be
  advertised as supported until it is validated, including a tested fully
  offline dependency bundle. Validation failures block support, not silently
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
  revoked, block web installation until local recovery. Do not promise that
  disconnected devices learn about revocation without trusted new information.
- Fully offline dependency-changing updates are a required manual alternative.
  Supply application and target-compatible dependency files separately where
  dependencies are externally supplied; online Pi updates download the same
  verified published bundle. A compatible ready environment can be reused.
  Windows' bundled-runtime package must also support a fully offline update;
  its precise package split remains a packaging decision.
- Allow an unsigned offline package only with explicit local command-line
  operator approval, a prominent warning and audit record; never expose this
  bypass through the web UI. Checksums cannot establish publisher identity.
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
- Define Windows architecture/OS coverage, exact package format, service
  account and elevation model, data directory, ACLs and uninstall policy.
  Verify BLE under that account on each supported configuration.
- Specify the practical settings/database compatibility guarantees and
  user-visible handling of incompatible or skipped releases. Use a simple
  compatibility generation plus tests, not a general release-pair rules engine.
- Define trust assumptions: unprivileged application compromise, admin
  authorization, updater/service account, local operators, release channel,
  network and filesystem. Unattended installation is outside initial scope.
- Specify offline artifact selection, provenance and manifest verification
  without GitHub access, and the precise approval and audit mechanics for
  unsigned packages. Do not silently treat an invalid signature as an unsigned
  package or bypass checks because a signature check failed.
- Choose signing technology and define canonical signed content, root-key
  provisioning, CI key permissions, compromise response, revocation
  distribution, expiry/freshness, replay and rollback protection, rotation,
  and offline behavior. A signed version number alone cannot prove that
  metadata is fresh, and old devices may need an operator bridge after key
  rotation. Assess CI-key theft and release-workflow compromise explicitly.
  Reuse an established signing format and verifier; do not design custom
  cryptography or a broad update-metadata framework without demonstrated need.

## Contracts to specify

1. Versioned manifest schema: stable tag grammar and version comparison,
   source commit, platform/architecture/package/runtime selectors, artifact
   size and digest, application version, updater compatibility, persisted
   data compatibility, release status and schema evolution. Define
   missing/unknown fields and fail-closed behavior, redirect and URL
   restrictions, GitHub rate-limit/offline behavior, and asset publication
   completeness. Specify whether older clients can read new manifests.
   Bind the application, dependency bundle, compatibility claims and hashes
   to authenticated release metadata for signed updates. Define signature
   verification and release completeness before any web installation.
2. Build lock/export policy: runtime versus development inputs, exact hashed
   transitive dependencies, target markers, selected interpreter and
   installer policy. Define reproducibility separately from artifact
   authenticity. Prove compatible installs and native dependency handling
   on the agreed matrix before committing to uv; compare pip-tools if needed.
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
  pre-per-version legacy install, a new installation, an offline operator
  and a skipped release. Exercise failed/interrupted bootstrap using genuine
  legacy install layouts and verify the recorded old launch configuration
  remains recoverable without relying on the new application.
- Threat-boundary review demonstrates the proposed privileged updater
  cannot be steered through app-writable metadata, symlinks, archive paths,
  scripts, environment variables or arbitrary commands. If not demonstrable,
  revise the boundary before implementing web-triggered updates.
- Record which target tests require real hardware or clean machines and
  where they will be run; no unsupported artifact is marked installable.
