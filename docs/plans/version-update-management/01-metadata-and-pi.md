# 01 - Release metadata and Pi environment reuse

Status: not started. Depends on the relevant [contracts](00-contracts.md).
Prepare release inputs and reusable environments. Production activation and
the existing-install transition are integrated once in plan 03.

## Independently reviewable milestones

1. **01a - uv lock/export:** authoritative project dependencies, committed
   `uv.lock`, generated hash-verified pip deployment requirements, and target
   installation tests. No updater changes.
2. **01b - Signed release metadata:** release manifest and signing using an
   established format/verifier. Test fetching release files and installing
   dependencies from the configured package index into disposable
   environments; no production activation.
3. **01c - Prepare immutable environments:** readiness validation, identity
   and release mappings, exercised with isolated fixtures and retained legacy
   environments. Use this implementation from plan 03; do not add a competing
   shell update engine as an intermediate deliverable.

## Scope

- Make `pyproject.toml` authoritative for Python support, runtime dependencies
  and separate development/test inputs. Use a committed `uv.lock` as the
  single lock and generate exact, hash-verified pip deployment requirements
  from it. Do not add pip-tools as a second supported lock workflow or maintain
  independently edited dependency lists. Validate uv resolution/export and
  pip installation across the agreed matrix; resolve target-specific issues
  before rollout rather than introducing a second workflow. Preserve or
  intentionally document developer and VS Code commands.
- Extend the release builder and CI to build release metadata, checksums,
  verified signatures and
  target-specific artifacts using the same stable tag and source commit.
  Avoid marking a release installable until its manifest and required assets
  exist and validate; handle both tag-push and `workflow_call` release paths.
  Retain notices and exclude private runtime data. Add Windows artifacts only
  when the Windows lifecycle plan has passed its packaging gates.
- Install from the generated, hash-verified runtime export using pip and a
  defined build policy. Validate exact transitive requirements on every
  supported target. Dependencies without compatible wheels may be built from
  the package index under that policy; do not require or publish target-specific
  wheel bundles. Do not imply `--require-hashes` alone makes source builds
  reproducible or publisher-authenticated.
- Implement focused environment preparation helpers. Build environments
  directly at final paths; atomically publish a readiness marker only after
  validation. Store durable release-to-environment mapping. Reuse an
  environment only when its identity and readiness match. Keep active,
  previous and other referenced environments immutable and protected.
  Plan 03 wires these helpers into install/update/rollback and pruning together.
- For dependency-changing updates, retrieve the signed application release
  and install its locked, hash-verified dependencies from the configured
  package index. Do not build, publish or transfer target-specific Pi
  dependency bundles. Reuse a compatible ready environment for code-only
  releases. New installs and updates require online retrieval and signed
  release metadata; preserve local rollback to retained releases.

## Boundary with bootstrap and activation

The deployed `update.sh` is the old script until a successful update refreshes
it. It currently stages `src`, `requirements.txt` and `VERSION`, builds a
per-version environment, and refreshes management scripts afterwards. It
does not automatically stage new lock exports or mapping files. Do not extend
this path to teach the old updater new contracts. Plan 03 introduces an
explicit operator bootstrap and then routes management through the new engine.

The first managed update may run pip and cannot promise a reusable environment
for an old unlocked install. Never infer that a legacy environment has the
new lock identity without proof. Establish an explicit mapping for legacy
releases, preserve their original venv and launch configuration, and test
rollback across the transition, including installs with a legacy single venv.
Do not promise rollback across an unavailable or changed system interpreter.
The one-time bootstrap itself requires network access to retrieve and verify
its signed inputs. Plan 03 validates the online bootstrap handoff against real
legacy layouts; this phase does not rely on old scripts understanding new
metadata or dependency exports.

## Acceptance

- Two release fixtures using identical effective
  runtime inputs reuse a validated environment; measured code-only preparation
  invokes no pip dependency installation. Changing only dev inputs or
  application version does not invalidate it.
- Changed runtime dependencies, Python ABI/version, OS or architecture do
  not reuse an incompatible environment. Failure during creation does not
  publish readiness or modify an active environment.
- Lock freshness, generated exports/hashes, tag/`VERSION`/manifest agreement,
  signature verification, asset completeness and selected-target installation
  are checked in CI.
  Hardware/native wheel coverage is reported separately from resolution.
- For each supported Pi target, validate uv lock export and package-index
  installation of locked dependencies, including native dependencies. Verify
  missing, incompatible, or hash-mismatched packages fail without changing
  active resources.
- Test failed preparation, reference protection and legacy environment
  preservation here. End-to-end install, update, rollback, pruning and
  operator bootstrap acceptance belongs to plan 03.
- Review `README.md`, `README-dev.md`, `README-pi.md`, `README-windows.md`
  and `docs/releases.md` for affected workflows, and update as needed.
