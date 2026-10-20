# Release maintenance

## Initial public release

The public repository starts with a clean source snapshot and `v1.1.0`.
Use the GitHub-provided noreply address for commit authorship when you want
to keep personal contact addresses out of public Git metadata.

1. Leave the repository Actions variable `AUTO_RELEASE_ENABLED` unset or
   `false` during initial setup. Main pushes still run Ubuntu/Windows CI
   and public CodeQL analysis, but they cannot automatically tag a release.
2. Configure read-only default Actions permissions and disable workflow
   approval of pull requests. Enable Dependabot alerts/security updates,
   secret scanning/push protection, and GitHub private vulnerability reporting.
   Verify the private reporting form linked in `SECURITY.md` and subscribe
   to repository security-alert notifications.
3. Push the reviewed source and require the `Tests (ubuntu-latest)` and
   `Tests (windows-latest)` checks. Protect `main` with pull requests and
   block force pushes and deletion. A solo maintainer need not require an
   approval they cannot obtain.
4. After both CI jobs and CodeQL pass, build and inspect the package:

   ```bash
   python scripts/build_release.py v1.1.0 --output-dir dist
   tar -tzf dist/ruuvihub-v1.1.0.tar.gz
   ```

   `LICENSE`, `SECURITY.md`, the bootstrap configuration, and complete
   third-party notices must accompany the application. The builder rejects
   private runtime files inside packaged folders.
5. Tag the reviewed initial `main` commit and push only that tag:

   ```bash
   git tag v1.1.0
   git push origin v1.1.0
   ```

   This invokes `release.yml`, which reruns the hardware-free tests and
   publishes the release archive. Check the uploaded artifact and release page.
6. Set `AUTO_RELEASE_ENABLED` to `true` under repository Settings → Secrets
   and variables → Actions → Variables when automatic patch releases are wanted.

## Later releases

With automatic releases enabled, a successful main push selects the next
patch after the highest stable `vX.Y.Z` tag. Commits already carrying a
stable release tag and stale main pushes are skipped. The next automatic
patch after `v1.1.0` is `v1.1.1`.

For deliberate major/minor releases, disable `AUTO_RELEASE_ENABLED` before
merging, wait for CI, push the intended stable version tag, verify the
release, and enable automatic patches again. This avoids competing manual
and automatic version tags on the same change.

Source checkouts report `dev`; CI-generated archives contain a `VERSION`
file with their tag. The live hardware test `tests/test_connection.py`
remains excluded from the normal CI suite.
