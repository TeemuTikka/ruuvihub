# 01 - Release metadata and Pi environment reuse

Status: not started. Depends on the relevant [contracts](00-contracts.md).
Prepare release inputs and reusable environments. Production activation and
the existing-install transition are integrated once in plan 03.

## Milestones

1. Establish `pyproject.toml`, `uv.lock`, and generated hash-verified pip
   deployment requirements. Validate install on the selected Pi environment.
2. Add only the signed release metadata and artifact checks needed by the
   installer/updater.
3. Implement simple dependency reuse: reuse the installed environment when
   its runtime lock identity matches; install a new environment when it does
   not. Plan 03 uses this behavior.

## Scope

- Make `pyproject.toml` authoritative for Python support, runtime dependencies
  and separate development/test inputs. Use a committed `uv.lock` as the
  single lock and generate exact, hash-verified pip deployment requirements
  from it. Do not maintain a second lock workflow or independently edited
  dependency lists. Preserve or document existing developer commands.
- Extend the release builder to publish signed metadata and application
  artifacts. Keep the metadata limited to version, supported artifact,
  minimum updater version, size and hash. Add Windows artifacts with plan 04.
- Install locked dependencies from the configured package index. Do not
  publish Pi dependency bundles. Record the lock identity for the installed
  environment so code-only releases can reuse it.

## Boundary with bootstrap and activation

The deployed `update.sh` is the old script until a successful update refreshes
it. Do not extend it to understand the new release metadata. Plan 03 uses an
explicit operator bootstrap to install the new Pi updater.

The first managed update may need to install dependencies because a legacy
environment has no recorded lock identity. Preserve its app and venv during
bootstrap so the operator can recover if the transition fails.

## Acceptance

- Verify lock freshness, generated hashes, version/manifest agreement,
  signature verification and artifact completeness in CI.
- Validate uv lock export and package-index installation on the selected
  64-bit Raspberry Pi OS environment. Record the OS and system Python versions
  used in the setup documentation; do not build a multi-version support
  matrix for personal use.
- Verify failed dependency installation does not replace the active
  environment. End-to-end update and bootstrap tests belong to plan 03.
- Review `README.md`, `README-dev.md`, `README-pi.md`, `README-windows.md`
  and `docs/releases.md` for affected workflows, and update as needed.
