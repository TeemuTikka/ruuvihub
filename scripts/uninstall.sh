#!/usr/bin/env bash
# scripts/uninstall.sh — remove a RuuviHub install from this Pi.
#
# Usage:
#   sudo ./uninstall.sh [--base-dir DIR] [--keep-data]
#
# By default this stops+disables the systemd service, removes the unit
# file, and deletes the ENTIRE install directory (including shared/ —
# settings.local.json, ruuvi_data.db, logs/, certs/, and the venv).
#
# Pass --keep-data to only remove the service and releases/, leaving
# shared/ in place — useful if you plan to reinstall later and want to
# keep your readings/settings. Re-running install.sh afterwards will
# detect and reuse the existing shared/config.json instead of
# recreating it.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

BASE_DIR="$RUUVIHUB_DEFAULT_BASE_DIR"
KEEP_DATA=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --base-dir) BASE_DIR="$2"; shift 2 ;;
        --keep-data) KEEP_DATA=1; shift ;;
        -h|--help)
            grep '^#' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) die "unknown argument: $1" ;;
    esac
done

require_root

if [[ ! -d "$BASE_DIR" && ! -f /etc/systemd/system/ruuvihub.service ]]; then
    die "nothing to uninstall: $BASE_DIR does not exist and no ruuvihub.service is installed."
fi

log "Stopping and disabling ruuvihub.service..."
systemctl stop ruuvihub.service 2>/dev/null || true
systemctl disable ruuvihub.service 2>/dev/null || true

if [[ -f /etc/systemd/system/ruuvihub.service ]]; then
    rm -f /etc/systemd/system/ruuvihub.service
    systemctl daemon-reload
    log "Removed /etc/systemd/system/ruuvihub.service"
else
    log "No systemd unit file found, skipping."
fi

if [[ "$KEEP_DATA" -eq 1 ]]; then
    log "Removing releases/ and current symlink, keeping shared/ (--keep-data)..."
    rm -f "$BASE_DIR/current"
    rm -rf "$BASE_DIR/releases"
    log "Kept: $BASE_DIR/shared (settings.local.json, ruuvi_data.db, logs/, certs/, venv)"
    log "Reinstall later with: sudo ./install.sh --base-dir $BASE_DIR"
else
    log "Removing $BASE_DIR entirely (including shared/ — settings, database, logs, venv)..."
    rm -rf "$BASE_DIR"
fi

log "RuuviHub uninstalled."
