# RuuviHub release and update plans

Status: reviewed planning only; implementation has not started. Updated with
the decisions confirmed during the interview on 11 November 2026. These are
temporary working documents on the feature branch and will be removed before
the pull request. The [original plan](original-plan.md) remains unchanged and
is the reference for background and alternatives.
If there is a conflict, the decisions and gates in these split plans take
precedence over the original's proposed sequence.

## Plan map

| Plan | Deliverable | Prerequisites |
|---|---|---|
| [00 - Contracts and decisions](00-contracts.md) | Supported targets, release, safety and lifecycle contracts; bridge design | None |
| [01 - Metadata and Pi environment reuse](01-metadata-and-pi.md) | Locked release inputs and faster, manually initiated Pi updates | Relevant decisions in 00 |
| [02 - Release notifications](02-notifications.md) | Non-blocking, admin-visible release checks, including dev checkouts | Version/checking decisions in 00; artifact metadata from 01 only for artifact-specific compatibility |
| [03 - External updater and Pi integration](03-updater-and-pi.md) | Durable, recoverable engine and independently managed Pi lifecycle | 00 and 01; not dependent on 02 |
| [04 - Windows production lifecycle](04-windows.md) | Packaged install, update and rollback on Windows | 00 and shared engine contract from 03 |
| [05 - Web update controls](05-web-controls.md) | Explicitly approved update/revert controls | 02, 03 and/or 04 per platform |

Implement and validate each phase as a separately reviewable change. Phase
02 can run in parallel with 01 once its own contracts are settled. Phase 05
must expose controls only for a platform whose adapter is actually ready.
Do not treat this plan as authorization to choose unresolved user-facing
policies or to publish unsupported targets.

## Review: material gaps corrected

1. **The current Pi updater cannot bootstrap its replacement in the same
   update.** `scripts/update.sh` executes the already installed script,
   copies only selected release inputs into the staged release, builds a
   per-version venv, and refreshes scripts after successful activation.
   New metadata or code cannot simply be assumed present at the expected
   path. Phase 01 requires a tested bridge and explicitly limits the
   zero-pip claim to releases managed by the new workflow.
2. **Privilege separation needs a concrete trust model.** The current
   systemd service runs as the service user, and the Pi updater stages code
   owned by that user. A privileged updater must not execute mutable
   app-controlled scripts or trust app-writable metadata, paths, or request
   fields. Phase 00 makes trust boundaries and installer/bootstrap integrity
   blocking design decisions; phases 03-05 test them. Checksums supplied
   by the same release channel are not independent publisher authentication.
3. **Rollback needs a runnable previous interpreter and compatible data,
   not merely old links.** Pi venvs depend on the underlying Python
   installation; a system upgrade can invalidate both releases. A release
   may also rewrite settings or data in a way the previous one cannot read.
   Contracts must define evidence and fail-closed eligibility, including
   skipped releases and manual revert.
4. **Crash recovery and contention must cover all control paths.** Existing
   shell update and rollback commands are separate from the proposed Python
   engine. A lock and journal are useful only if all operations, including
   local commands and recovery, use the same protocol; interrupted two-link
   activation and concurrent legacy commands need an explicit migration.
5. **Lockfiles do not establish supported binaries or compatibility.**
   Windows, Pi architecture and Python support, native wheels/build
   dependencies, exported hashes, pip policy, and environment identity
   require actual target validation. Do not equate lock resolution with
   successful installation.
6. **Windows is not currently a production install.** Its account, ACLs,
   native BLE behavior, packaging details and independent
   recovery remain design and hardware acceptance gates, not assumptions.
7. **Publication and update UX need intermediate states.** Publish a
   coherent release only after required assets exist; notifications remain
   useful if artifacts are unavailable. Status must distinguish stale data,
   incompatible releases, failed updates, successful rollback and failed
   rollback. A health check is a startup check, not proof of long-term health.
8. **Signing and full offline delivery are now initial requirements.**
   Remote web installation requires verified signatures, not just checksums.
   Every supported Pi target needs a tested offline dependency bundle, even
   though online updates are the default. The existing updater cannot provide
   these guarantees during its own bootstrap without an explicit bridge.
9. **Trusted-key rotation is not compromise recovery.** A rotation signed by
   a still-trusted key is useful for planned rotation; a compromised key cannot
   be the sole trust anchor for its own replacement. Revocation reachability,
   replay/freshness and offline limitations remain to be designed.
10. **Documented support is broader than an enumerated matrix.** Pi 3, 4,
    Zero W, Bullseye or newer, and Python 3.10+ are to be preserved, but these
    labels do not enumerate finite architectures, OS/Python combinations or
    future versions. Phase 00 must establish and validate an operational
    matrix without silently narrowing this commitment.

## Cross-cutting completion rules

- Keep the documented manual/offline Pi workflow and a local recovery path.
  Do not advertise web updates on development checkouts.
- Review the appropriate repository READMEs and release guide with each
  implementation change. Keep source-checkout/F5 workflows working or
  document an intentional change.
- Run focused tests first, then the documented hardware-free suite
  `python -m pytest tests/ -q --ignore=tests/test_connection.py` for each
  implementation phase. Run platform/hardware tests when available; identify
  missing evidence as a release blocker rather than claiming success.
- If shell scripts change, verify LF line endings and run `bash -n` for each
  changed script.
- Record decisions, actual CI/hardware coverage, transition limitations and
  any release blockers in the relevant plan before implementation is called
  complete. No application code has been changed here.
