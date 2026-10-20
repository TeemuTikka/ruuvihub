#!/usr/bin/env bash
# scripts/update.sh — update an existing RuuviHub install in place.
#
# Usage:
#   sudo ./update.sh /path/to/ruuvihub-vX.Y.Z.tar.gz [--base-dir DIR] [--user USER] [--keep N]
#
# Downloaded/scp'd the new release archive to the Pi yourself first
# (see README-pi.md "Updating"). This script then:
#   1. Extracts it into a NEW releases/<VERSION>/ directory
#   2. Builds a fresh, release-specific virtualenv (leaving the
#      previous release's venv untouched, for atomic rollback)
#   3. Ensures shared/certs/ has a persisted TLS certificate and that
#      settings.local.json points at it (a one-time migration for
#      installs from before HTTPS existed -- a no-op otherwise). Any
#      pre-existing shared/ruuvihub.log is migrated onto the new
#      shared/logs/ layout automatically at application startup (see
#      src/main.py's _migrate_log_path_to_logs_subfolder), not here --
#      a shell-only migration step can't run during the very update
#      that introduces it (see that function's docstring for why).
#   4. Stops the service, flips the `current` and `shared/venv`
#      symlinks together, restarts it
#   5. Verifies the new version is healthy (systemctl + /healthz,
#      including that /healthz reports the expected new version)
#   6. Automatically rolls back to the previous release (code AND
#      venv) if it isn't
#   7. Prunes old release directories and virtualenvs beyond --keep
#      (default 3)
#
# settings.local.json, ruuvi_data.db, logs/, and certs/ live in
# shared/ and persist across updates; this script only ever adds the
# TLS cert paths in settings.local.json when needed (step 3 above) and
# never otherwise rewrites these files. (log_path migrates itself at
# application startup instead -- see step 3's note above.) Each
# release's dependencies live in their own shared/venvs/<version>/
# directory instead of a single mutated shared venv.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

ARCHIVE=""
BASE_DIR="$RUUVIHUB_DEFAULT_BASE_DIR"
SERVICE_USER="$RUUVIHUB_DEFAULT_USER"
KEEP="$RUUVIHUB_DEFAULT_KEEP"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --base-dir) BASE_DIR="$2"; shift 2 ;;
        --user) SERVICE_USER="$2"; shift 2 ;;
        --keep) KEEP="$2"; shift 2 ;;
        -h|--help)
            grep '^#' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            [[ -z "$ARCHIVE" ]] || die "unexpected extra argument: $1"
            ARCHIVE="$1"
            shift
            ;;
    esac
done

require_root
[[ -n "$ARCHIVE" ]] || die "usage: $0 /path/to/ruuvihub-vX.Y.Z.tar.gz [--base-dir DIR] [--user USER] [--keep N]"
[[ -f "$ARCHIVE" ]] || die "archive not found: $ARCHIVE"
[[ -L "$BASE_DIR/current" ]] || die "$BASE_DIR has no existing install (no 'current' symlink). Run scripts/install.sh first."

PREV_RELEASE_DIR="$(readlink -f "$BASE_DIR/current")"
PREV_VERSION="$(basename "$PREV_RELEASE_DIR")"

EXTRACT_DIR="$(mktemp -d)"
trap 'rm -rf "$EXTRACT_DIR"' EXIT
# mktemp -d creates the directory mode 0700, owned by root (this script
# requires root). Below, generate_cert.py/persist_tls_paths.py are run
# as_service_user (sudo -u "$SERVICE_USER", e.g. "pi") against files
# under here -- without relaxing this, that non-root user can't even
# traverse into $EXTRACT_DIR to open them, failing with EACCES/EPERM
# ("Permission denied") on persist_tls_paths.py.
chmod 755 "$EXTRACT_DIR"
log "Extracting $ARCHIVE..."
tar -xzf "$ARCHIVE" -C "$EXTRACT_DIR"

# Support both "archive contains its files at the root" and "archive
# contains one top-level folder" layouts produced by different tar options.
WORK_DIR="$EXTRACT_DIR"
if [[ ! -d "$WORK_DIR/src" ]]; then
    INNER_DIR="$(find "$EXTRACT_DIR" -mindepth 1 -maxdepth 1 -type d | head -n1)"
    [[ -n "$INNER_DIR" && -d "$INNER_DIR/src" ]] || die "archive does not contain a src/ directory — is this a valid RuuviHub release archive?"
    WORK_DIR="$INNER_DIR"
fi

NEW_VERSION="$(read_version_file "$WORK_DIR")"
[[ "$NEW_VERSION" != "$PREV_VERSION" ]] || die "release $NEW_VERSION is already installed (current -> $PREV_RELEASE_DIR)."

NEW_RELEASE_DIR="$BASE_DIR/releases/$NEW_VERSION"
[[ ! -d "$NEW_RELEASE_DIR" ]] || die "releases/$NEW_VERSION already exists. Remove it manually first if you want to re-install it."

log "Staging $NEW_VERSION at $NEW_RELEASE_DIR..."
mkdir -p "$NEW_RELEASE_DIR"
cp -r "$WORK_DIR/src" "$NEW_RELEASE_DIR/src"
cp "$WORK_DIR/requirements.txt" "$NEW_RELEASE_DIR/requirements.txt"
echo "$NEW_VERSION" > "$NEW_RELEASE_DIR/VERSION"
chown -R "$SERVICE_USER":"$SERVICE_USER" "$NEW_RELEASE_DIR"

log "Building virtualenv for $NEW_VERSION..."
build_release_venv "$NEW_VERSION" "$NEW_RELEASE_DIR/requirements.txt"

# Migration for installs from before HTTPS existed (or any install still
# missing a shared certificate): shared/certs/ and a persisted
# tls_cert_path/tls_key_path in settings.local.json must exist BEFORE
# the new release starts. Without this, an existing settings.local.json
# has empty TLS paths, so run_web_server falls back to generating a
# *new* self-signed cert inside this release's own directory -- and
# does so again on every future update -- invalidating the previously
# trusted certificate each time instead of persisting it like
# settings.local.json/ruuvi_data.db/logs do.
log "Ensuring shared TLS certificate exists and is persisted in settings..."
mkdir -p "$BASE_DIR/shared/certs"
chown -R "$SERVICE_USER":"$SERVICE_USER" "$BASE_DIR/shared/certs"
if [[ ! -f "$BASE_DIR/shared/certs/ruuvihub.crt" || ! -f "$BASE_DIR/shared/certs/ruuvihub.key" ]]; then
    # Uses the NEW release's venv/helper script -- the currently-active
    # (old) release's scripts/venv may predate HTTPS entirely.
    as_service_user "$(venv_dir_for_version "$NEW_VERSION")/bin/python3" "$WORK_DIR/scripts/generate_cert.py" \
        "$NEW_RELEASE_DIR/src" \
        "$BASE_DIR/shared/certs/ruuvihub.crt" \
        "$BASE_DIR/shared/certs/ruuvihub.key"
fi
if [[ -f "$BASE_DIR/shared/settings.local.json" ]]; then
    as_service_user python3 "$WORK_DIR/scripts/persist_tls_paths.py" \
        "$BASE_DIR/shared/settings.local.json" \
        "$BASE_DIR/shared/certs/ruuvihub.crt" \
        "$BASE_DIR/shared/certs/ruuvihub.key"
fi

rollback() {
    warn "Update to $NEW_VERSION failed health check — rolling back to $PREV_VERSION."
    systemctl stop ruuvihub.service || true
    ln -sfn "$PREV_RELEASE_DIR" "$BASE_DIR/current"
    chown -h "$SERVICE_USER":"$SERVICE_USER" "$BASE_DIR/current"
    activate_venv "$PREV_VERSION"
    systemctl start ruuvihub.service || true
    if wait_for_healthy "$PREV_VERSION"; then
        warn "Rolled back to $PREV_VERSION successfully. $NEW_VERSION was left at $NEW_RELEASE_DIR (venv: $(venv_dir_for_version "$NEW_VERSION")) for inspection (sudo journalctl -u ruuvihub -n 200)."
    else
        die "Rollback to $PREV_VERSION ALSO failed health check. Manual intervention required: sudo journalctl -u ruuvihub -n 200"
    fi
    exit 1
}

log "Switching current -> releases/$NEW_VERSION and activating its virtualenv..."
systemctl stop ruuvihub.service || true
ln -sfn "$NEW_RELEASE_DIR" "$BASE_DIR/current"
chown -h "$SERVICE_USER":"$SERVICE_USER" "$BASE_DIR/current"
activate_venv "$NEW_VERSION"
# Not "set -e"-guarded: a systemctl start failure must still fall through
# to the health check below (which will then trigger rollback), rather
# than exiting the script with `current` already pointed at the new,
# unverified release.
systemctl start ruuvihub.service || true

if ! wait_for_healthy "$NEW_VERSION"; then
    rollback
fi

log "RuuviHub updated: $PREV_VERSION -> $NEW_VERSION"

log "Refreshing management scripts at $BASE_DIR/scripts..."
mkdir -p "$BASE_DIR/scripts"
# This script (update.sh) is itself one of the files being refreshed here,
# and it is still running as $BASE_DIR/scripts/update.sh while this loop
# executes. An in-place `cp` (which truncates and rewrites the same
# inode/file) can corrupt bash's read-ahead buffer of the still-running
# script, causing spurious crashes (e.g. "syntax error near unexpected
# token") after this point even though the update itself already
# succeeded. `mv`/rename() instead swaps a directory entry to point at a
# NEW inode -- the currently-running process keeps its original (now
# unlinked but still open) inode via its file descriptor, completely
# unaffected. So refresh via write-to-temp-then-atomic-rename instead of
# overwriting each destination file in place.
atomic_replace() {
    local src="$1"
    local dest="$2"
    local tmp="${dest}.tmp.$$"
    cp -p "$src" "$tmp"
    mv -f "$tmp" "$dest"
}
for src in "$WORK_DIR"/scripts/*.sh; do
    atomic_replace "$src" "$BASE_DIR/scripts/$(basename "$src")"
done
atomic_replace "$WORK_DIR/scripts/ruuvihub.service.template" "$BASE_DIR/scripts/ruuvihub.service.template"
atomic_replace "$WORK_DIR/scripts/generate_cert.py" "$BASE_DIR/scripts/generate_cert.py"
atomic_replace "$WORK_DIR/scripts/persist_tls_paths.py" "$BASE_DIR/scripts/persist_tls_paths.py"
[[ -f "$WORK_DIR/scripts/test_connection.py" ]] && atomic_replace "$WORK_DIR/scripts/test_connection.py" "$BASE_DIR/scripts/test_connection.py"
chmod +x "$BASE_DIR/scripts/"*.sh
chown -R "$SERVICE_USER":"$SERVICE_USER" "$BASE_DIR/scripts"

log "Pruning old releases (keeping $KEEP most recent, plus $PREV_VERSION)..."
mapfile -t ALL_RELEASES < <(find "$BASE_DIR/releases" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort_versions)
TO_KEEP=("$NEW_VERSION" "$PREV_VERSION")
# Keep the last KEEP versions by version order, in addition to the two above.
mapfile -t RECENT < <(printf '%s\n' "${ALL_RELEASES[@]}" | sort_versions | tail -n "$KEEP")
TO_KEEP+=("${RECENT[@]}")

for dir_name in "${ALL_RELEASES[@]}"; do
    keep=0
    for keep_name in "${TO_KEEP[@]}"; do
        [[ "$dir_name" == "$keep_name" ]] && keep=1 && break
    done
    if [[ "$keep" -eq 0 ]]; then
        log "Removing old release $dir_name"
        rm -rf "${BASE_DIR:?}/releases/${dir_name:?}"
    fi
done

log "Pruning old virtualenvs (mirroring the releases kept above)..."
if [[ -d "$BASE_DIR/shared/venvs" ]]; then
    mapfile -t ALL_VENVS < <(find "$BASE_DIR/shared/venvs" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort_versions)
    for dir_name in "${ALL_VENVS[@]}"; do
        keep=0
        for keep_name in "${TO_KEEP[@]}"; do
            [[ "$dir_name" == "$keep_name" ]] && keep=1 && break
        done
        if [[ "$keep" -eq 0 ]]; then
            log "Removing old virtualenv $dir_name"
            rm -rf "${BASE_DIR:?}/shared/venvs/${dir_name:?}"
        fi
    done
fi

HEALTH_URL="$(health_check_url)"
CURL_OPTS=(-sf)
[[ "$HEALTH_URL" == https://* ]] && CURL_OPTS+=(-k)
log "Done. Running version: $(curl "${CURL_OPTS[@]}" "$HEALTH_URL" || true)"
