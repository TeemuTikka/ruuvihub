# 01 - Release metadata and Pi environment reuse

Status: not started. Depends on the relevant [contracts](00-contracts.md).
Prepare release inputs and reusable environments. Production activation and
the existing-install transition are integrated once in plan 03.

## Independently reviewable milestones

1. **01a - Lock inputs:** authoritative project dependencies, a validated
   lock/export workflow, and target installation tests. No updater changes.
2. **01b - Build bundles and metadata:** target dependency bundles, release
   manifest and signing using an established format/verifier. Test disconnected
   installation into disposable environments; no production activation.
3. **01c - Prepare immutable environments:** readiness validation, identity
   and release mappings, exercised with isolated fixtures and retained legacy
   environments. Use this implementation from plan 03; do not add a competing
   shell update engine as an intermediate deliverable.

## Scope

- Make `pyproject.toml` authoritative for Python support, runtime
  dependencies and separate development/test inputs. Prefer a committed
  `uv.lock` and generated, exact, hash-pinned pip exports after proving the
  agreed matrix. Use the evaluated pip-tools fallback if that proof fails.
  Do not maintain multiple independently edited dependency lists. Preserve
  or intentionally document developer and VS Code commands.
- Extend the release builder and CI to build release metadata, checksums,
  verified signatures and
  target-specific artifacts using the same stable tag and source commit.
  Avoid marking a release installable until its manifest and required assets
  exist and validate; handle both tag-push and `workflow_call` release paths.
  Retain notices and exclude private runtime data. Add Windows artifacts only
  when the Windows lifecycle plan has passed its packaging gates.
- Install from the generated, hashed runtime export with a defined pip and
  build policy. Validate environment markers and exact transitive requirements
  on each supported target. Account for source builds and their build
  dependencies; do not imply `--require-hashes` alone makes builds
  reproducible or publisher-authenticated.
- Implement focused environment preparation helpers. Build environments
  directly at final paths; atomically publish a readiness marker only after
  validation. Store durable release-to-environment mapping. Reuse an
  environment only when its identity and readiness match. Keep active,
  previous and other referenced environments immutable and protected.
  Plan 03 wires these helpers into install/update/rollback and pruning together.
- Publish and test a separate target-specific offline dependency bundle for
  each supported Pi target. Online dependency-changing updates fetch the
  same bundle using authenticated metadata; offline operators transfer it
  with the application archive. Install without contacting a package index
  during the offline workflow, including build dependencies. Reuse an
  existing compatible environment for code-only releases.
- Cache packages where appropriate without treating an unverified cache as
  trusted. Keep local archive installation and rollback. Signed metadata is
  normal; an explicitly approved local-only unsigned exception is covered by
  the updater contract.

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
Offline inputs must include everything needed to prepare the new environment.
Plan 03 validates bootstrap trust and the fully offline handoff against real
legacy layouts; this phase does not rely on old scripts verifying signatures
or consuming the new bundles.

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
- For each supported Pi target, build and test dependency-changing preparation
  with networking disabled and only the transferred application and
  dependency files. Verify missing/wrong bundles fail without changing active
  resources.
- Test failed preparation, reference protection and legacy environment
  preservation here. End-to-end install, update, rollback, pruning and
  operator bootstrap acceptance belongs to plan 03.
- Review `README.md`, `README-dev.md`, `README-pi.md`, `README-windows.md`
  and `docs/releases.md` for affected workflows, and update as needed.
