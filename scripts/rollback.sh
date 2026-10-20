#!/usr/bin/env bash
# scripts/rollback.sh — manually roll back to a previously installed
# RuuviHub release (on-demand, independent of update.sh's automatic
# rollback-on-failed-health-check).
#
# Usage:
#   sudo ./rollback.sh [VERSION] [--base-dir DIR] [--user USER]
#
# If VERSION is omitted, rolls back to the most recent release older
# than the currently active one (i.e. "undo the last update").
# Run with no VERSION and no other releases installed to just see the
# error listing what's available.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

BASE_DIR="$RUUVIHUB_DEFAULT_BASE_DIR"
SERVICE_USER="$RUUVIHUB_DEFAULT_USER"
TARGET_VERSION=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --base-dir) BASE_DIR="$2"; shift 2 ;;
        --user) SERVICE_USER="$2"; shift 2 ;;
        -h|--help)
            grep '^#' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            [[ -z "$TARGET_VERSION" ]] || die "unexpected extra argument: $1"
            TARGET_VERSION="$1"
            shift
            ;;
    esac
done

require_root
[[ -L "$BASE_DIR/current" ]] || die "$BASE_DIR has no existing install (no 'current' symlink)."

CURRENT_VERSION="$(basename "$(readlink -f "$BASE_DIR/current")")"
CURRENT_RELEASE_DIR="$(readlink -f "$BASE_DIR/current")"
mapfile -t ALL_RELEASES < <(find "$BASE_DIR/releases" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort_versions)

if [[ -z "$TARGET_VERSION" ]]; then
    # Default: the release immediately before the current one, in version order.
    PREV=""
    for name in "${ALL_RELEASES[@]}"; do
        [[ "$name" == "$CURRENT_VERSION" ]] && break
        PREV="$name"
    done
    [[ -n "$PREV" ]] || die "no earlier release found to roll back to. Available: ${ALL_RELEASES[*]}"
    TARGET_VERSION="$PREV"
fi

TARGET_DIR="$BASE_DIR/releases/$TARGET_VERSION"
[[ -d "$TARGET_DIR" ]] || die "releases/$TARGET_VERSION does not exist. Available: ${ALL_RELEASES[*]}"
[[ "$TARGET_VERSION" != "$CURRENT_VERSION" ]] || die "$TARGET_VERSION is already the active release."

log "Rolling back $CURRENT_VERSION -> $TARGET_VERSION..."
systemctl stop ruuvihub.service || true
ln -sfn "$TARGET_DIR" "$BASE_DIR/current"
chown -h "$SERVICE_USER":"$SERVICE_USER" "$BASE_DIR/current"
activate_venv "$TARGET_VERSION"
# Not "set -e"-guarded: a systemctl start failure must still fall
# through to the health check below rather than exiting with `current`
# left pointed at an unverified/failed target.
systemctl start ruuvihub.service || true

if wait_for_healthy "$TARGET_VERSION"; then
    log "Rolled back to $TARGET_VERSION successfully."
else
    warn "Rollback to $TARGET_VERSION failed its health check — restoring $CURRENT_VERSION."
    systemctl stop ruuvihub.service || true
    ln -sfn "$CURRENT_RELEASE_DIR" "$BASE_DIR/current"
    chown -h "$SERVICE_USER":"$SERVICE_USER" "$BASE_DIR/current"
    activate_venv "$CURRENT_VERSION"
    systemctl start ruuvihub.service || true
    if wait_for_healthy "$CURRENT_VERSION"; then
        die "Rollback to $TARGET_VERSION failed its health check; restored $CURRENT_VERSION, which is running again. Check: sudo journalctl -u ruuvihub -n 200"
    else
        die "Rollback to $TARGET_VERSION failed AND restoring $CURRENT_VERSION also failed its health check. Manual intervention required: sudo journalctl -u ruuvihub -n 200"
    fi
fi
