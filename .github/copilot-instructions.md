# Copilot Instructions for RuuviHub

## Documentation stays in sync with code

This repo has multiple README files, each covering a different concern:

| File | Covers |
|---|---|
| `README.md` | What RuuviHub does, general/cross-platform settings |
| `README-dev.md` | Project structure, key classes, architecture, local debugging |
| `README-windows.md` | Installing/running on Windows (development) |
| `README-pi.md` | Installing/running as a systemd service on Raspberry Pi (production) |

**Whenever you make a code change, you must:**

1. **Review** the README(s) relevant to the area you touched (architecture,
   settings, install/run steps, project structure, dependencies, etc.) to
   check whether they still describe the current behavior.
2. **Update** any README that is now inaccurate or incomplete because of
   your change — e.g. new/removed settings, changed defaults, new
   scripts or endpoints, new dependencies, changed file/module layout,
   or changed install/run/service steps.
3. If a change is purely internal (no user- or developer-facing behavior
   change), it's fine to leave the docs untouched — but say so explicitly
   in your summary (e.g. "No README updates needed: internal refactor
   only").
4. Prefer editing the most specific README for the change (e.g. Pi service
   steps go in `README-pi.md`, architecture/code-structure notes go in
   `README-dev.md`) rather than duplicating content across files.
5. Keep documentation changes scoped to what the code change actually
   affects — don't rewrite unrelated sections.

Never submit or finish a task that changes behavior, configuration,
dependencies, or setup steps without having checked the READMEs above.

## Shell scripts (`scripts/*.sh`) must stay LF

`.gitattributes` enforces `*.sh text eol=lf` because bash on the Pi
fails with `$'\r': command not found` / "unexpected token" errors on
CRLF scripts. Editing tools on Windows can silently reintroduce CRLF.

**Whenever you edit a `.sh` file:**
1. After editing, verify line endings didn't flip to CRLF — e.g.
   `git diff --stat` should show a normal line-count diff, not a
   "CRLF will be replaced by LF" warning from git, and
   `(Get-Content <file> -Raw) -match "`r`n"` should be `False`.
2. If CRLF crept in, normalize back to LF before committing.
3. Prefer verifying syntax with `bash -n scripts/<file>.sh` (via WSL if
   on Windows, since native Windows bash isn't available) rather than
   assuming the edit is correct.

## Migrating persisted install-time settings

Some `Settings`/`Config` fields (e.g. `db_path`, `log_path`,
`tls_cert_path`) are file-only and get baked into
`shared/settings.local.json` once, at first install, by
`scripts/install.sh`. Because `settings.local.json` is treated as the
permanent source of truth and is **never** re-bootstrapped from
`config.json` on later runs, changing one of these defaults (e.g.
moving where log files live) has **no effect on existing Pi installs**
unless something also migrates it.

**Two valid migration patterns — pick based on when the new value must
exist:**

1. **Shell-script migration (`scripts/update.sh`)** — required when the
   new release's *own startup* depends on the value already being
   correct (e.g. `tls_cert_path`/`tls_key_path`: the HTTPS server needs
   a persisted cert *before* it starts, or it'll generate — and
   re-invalidate — a new self-signed one every update). Mirror the
   existing `persist_tls_paths.py` pattern: an idempotent script under
   `scripts/` that only rewrites the field if it still matches the old
   default, wired into `update.sh` and added to both `install.sh`'s and
   `update.sh`'s copied-scripts lists, with its own unit tests.
   **Caveat:** this can't take effect during the very update that
   introduces it — the documented update flow runs the
   *previously-installed* `shared/scripts/update.sh`, which doesn't
   contain the new migration logic yet, so it only takes effect
   starting with the update *after* that one.
2. **Application-startup migration (in `main.py`, e.g.
   `_migrate_log_path_to_logs_subfolder`)** — preferred whenever the
   migration doesn't need to happen before the app's own process
   starts (e.g. `log_path`: the app can safely move the file and
   reconfigure logging in the same startup that also serves everything
   else). Running it from Python at every `main()` startup as a cheap
   idempotent no-op guarantees it applies starting with the exact
   release that introduces the new default, regardless of how the
   service was deployed/updated — it isn't tied to which version of
   `update.sh` happened to run it.

**Whenever you change a default for one of these file-only fields**,
decide which pattern applies, implement it, and add unit tests
covering: the migration actually moving/rewriting the value, and it
being a no-op when the value already matches the new default (or is an
operator's own custom value).
