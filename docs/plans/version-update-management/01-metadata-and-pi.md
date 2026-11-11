# 01 - Release metadata and Pi environment reuse

Status: not started. Depends on the relevant [contracts](00-contracts.md).
Deliver a safe manual-update improvement without requiring the web updater.

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
- Change Pi install/update/rollback and pruning together. Build environments
  directly at final paths; atomically publish a readiness marker only after
  validation. Store durable release-to-environment mapping. Reuse an
  environment only when its identity and readiness match. Keep active,
  previous and other referenced environments immutable and protected.
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

## Bootstrap and compatibility

The deployed `update.sh` is the old script until a successful update refreshes
it. It currently stages `src`, `requirements.txt` and `VERSION`, builds a
per-version environment, and refreshes management scripts afterwards. It
does not automatically stage new lock exports or mapping files. Design and
test the bridge against that exact behavior, including the previous script's
pruning and rollback behavior. A release may need to carry compatibility
inputs or require documented operator bootstrap; do not assume the new
updater executes during the update that introduces it.

The first bridge update may run pip and cannot promise a reusable environment
for an old unlocked install. Never infer that a legacy environment has the
new lock identity without proof. Establish an explicit mapping for legacy
releases, preserve their original venv and launch configuration, and test
rollback across the transition, including installs with a legacy single venv.
Do not promise rollback across an unavailable or changed system interpreter.
The first bridge's full offline behavior and trust verification must be
demonstrated using the actual previously installed scripts, or use a
documented operator bootstrap. Do not claim that the old script can verify
signatures or use the new bundles without evidence.

## Acceptance

- Two successive releases using the new workflow and identical effective
  runtime inputs reuse a validated environment; a measured code-only update
  invokes no pip dependency installation. Changing only dev inputs or
  application version does not invalidate it.
- Changed runtime dependencies, Python ABI/version, OS or architecture do
  not reuse an incompatible environment. Failure during creation does not
  publish readiness or modify an active environment.
- Lock freshness, generated exports/hashes, tag/`VERSION`/manifest agreement,
  signature verification, asset completeness and selected-target installation
  are checked in CI.
  Hardware/native wheel coverage is reported separately from resolution.
- For each supported Pi target, build and test a dependency-changing update
  with networking disabled and only the transferred application and
  dependency files. Verify missing/wrong bundles fail before activation.
- Test install, update, manual rollback, failed preparation/activation,
  repeated failures, pruning by references, offline/cache limitations and
  legacy bridge using the actual previously installed scripts.
- Review `README.md`, `README-dev.md`, `README-pi.md`, `README-windows.md`
  and `docs/releases.md` for affected workflows, and update as needed.
