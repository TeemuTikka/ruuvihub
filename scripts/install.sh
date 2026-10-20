#!/usr/bin/env bash
# scripts/install.sh — first-time RuuviHub install on a Raspberry Pi.
#
# Run this from inside an extracted release archive
# (ruuvihub-vX.Y.Z.tar.gz from https://github.com/TeemuTikka/ruuvihub/releases),
# transferred to the Pi via scp/USB/curl. No git or GitHub access is
# required on the Pi itself.
#
# Usage:
#   sudo ./scripts/install.sh [--base-dir DIR] [--user USER] [--skip-apt]
#
# Sets up:
#   $BASE_DIR/releases/<VERSION>/   - this release's code
#   $BASE_DIR/shared/               - config.json, settings.local.json,
#                                      ruuvi_data.db, logs/ (daily log files),
#                                      certs/ (self-signed HTTPS cert), venv/
#   $BASE_DIR/current                - symlink to the active release
#   /etc/systemd/system/ruuvihub.service
#
# See README-pi.md for the full walkthrough.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

ARCHIVE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BASE_DIR="$RUUVIHUB_DEFAULT_BASE_DIR"
SERVICE_USER="$RUUVIHUB_DEFAULT_USER"
SKIP_APT=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --base-dir) BASE_DIR="$2"; shift 2 ;;
        --user) SERVICE_USER="$2"; shift 2 ;;
        --skip-apt) SKIP_APT=1; shift ;;
        -h|--help)
            grep '^#' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) die "unknown argument: $1" ;;
    esac
done

require_root

if [[ -L "$BASE_DIR/current" ]]; then
    die "$BASE_DIR already has an installed release (current -> $(readlink "$BASE_DIR/current")). Use scripts/update.sh instead."
fi

[[ -d "$ARCHIVE_ROOT/src" ]] || die "src/ not found next to scripts/ — run this from inside the extracted release archive."

VERSION="$(read_version_file "$ARCHIVE_ROOT")"
log "Installing RuuviHub $VERSION to $BASE_DIR (service user: $SERVICE_USER)"

if ! id "$SERVICE_USER" &>/dev/null; then
    die "user '$SERVICE_USER' does not exist. Create it first or pass --user."
fi

if [[ "$SKIP_APT" -eq 0 ]]; then
    log "Installing system packages (bluetooth, bluez, python3-venv)..."
    apt-get update
    apt-get install -y bluetooth bluez python3-venv
else
    log "Skipping apt-get (--skip-apt)."
fi

log "Creating directory layout..."
mkdir -p "$BASE_DIR/releases" "$BASE_DIR/shared"
chown -R "$SERVICE_USER":"$SERVICE_USER" "$BASE_DIR"

log "Installing management scripts to $BASE_DIR/scripts..."
mkdir -p "$BASE_DIR/scripts"
cp "$SCRIPT_DIR/"*.sh "$SCRIPT_DIR/ruuvihub.service.template" "$BASE_DIR/scripts/"
cp "$SCRIPT_DIR/generate_cert.py" "$SCRIPT_DIR/persist_tls_paths.py" "$BASE_DIR/scripts/"
[[ -f "$SCRIPT_DIR/test_connection.py" ]] && cp "$SCRIPT_DIR/test_connection.py" "$BASE_DIR/scripts/"
chmod +x "$BASE_DIR/scripts/"*.sh
chown -R "$SERVICE_USER":"$SERVICE_USER" "$BASE_DIR/scripts"

RELEASE_DIR="$BASE_DIR/releases/$VERSION"
log "Copying release files to $RELEASE_DIR..."
mkdir -p "$RELEASE_DIR"
cp -r "$ARCHIVE_ROOT/src" "$RELEASE_DIR/src"
cp "$ARCHIVE_ROOT/requirements.txt" "$RELEASE_DIR/requirements.txt"
echo "$VERSION" > "$RELEASE_DIR/VERSION"
chown -R "$SERVICE_USER":"$SERVICE_USER" "$RELEASE_DIR"

if [[ ! -f "$BASE_DIR/shared/config.json" ]]; then
    log "Bootstrapping shared/config.json (first install)..."
    cp "$ARCHIVE_ROOT/config.json" "$BASE_DIR/shared/config.json"
    python3 - "$BASE_DIR/shared/config.json" "$BASE_DIR/shared/ruuvi_data.db" "$BASE_DIR/shared/logs/ruuvihub.log" "$BASE_DIR/shared/certs/ruuvihub.crt" "$BASE_DIR/shared/certs/ruuvihub.key" <<'PY'
import json
import sys

config_path, db_path, log_path, cert_path, key_path = sys.argv[1:6]
with open(config_path, "r", encoding="utf-8") as f:
    data = json.load(f)
data["db_path"] = db_path
data["log_path"] = log_path
data["tls_cert_path"] = cert_path
data["tls_key_path"] = key_path
with open(config_path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
PY
    chown "$SERVICE_USER":"$SERVICE_USER" "$BASE_DIR/shared/config.json"
else
    log "shared/config.json already exists, leaving it as-is."
fi

VENV_DIR="$(venv_dir_for_version "$VERSION")"
if [[ ! -d "$VENV_DIR" ]]; then
    log "Building virtualenv for $VERSION..."
    build_release_venv "$VERSION" "$RELEASE_DIR/requirements.txt"
else
    log "Virtualenv for $VERSION already exists, leaving it as-is."
fi
# Always (re)point shared/venv at this release's own venv -- never trust
# an arbitrary pre-existing shared/venv (e.g. a legacy single venv
# retained by `uninstall.sh --keep-data`), since that could be missing
# this release's dependencies (like `cryptography`, needed just below).
activate_venv "$VERSION"

if [[ ! -f "$BASE_DIR/shared/certs/ruuvihub.crt" || ! -f "$BASE_DIR/shared/certs/ruuvihub.key" ]]; then
    log "Generating self-signed HTTPS certificate in shared/certs/..."
    mkdir -p "$BASE_DIR/shared/certs"
    chown -R "$SERVICE_USER":"$SERVICE_USER" "$BASE_DIR/shared/certs"
    # Run as the service account (not root) via the release's own venv,
    # with paths passed as argv rather than interpolated into a heredoc
    # script -- importing/executing release code (tls.py) as root would
    # let a compromised service account gain root code execution.
    as_service_user "$BASE_DIR/shared/venv/bin/python3" "$SCRIPT_DIR/generate_cert.py" \
        "$RELEASE_DIR/src" \
        "$BASE_DIR/shared/certs/ruuvihub.crt" \
        "$BASE_DIR/shared/certs/ruuvihub.key"
else
    log "shared/certs/ruuvihub.crt already exists, leaving it as-is."
fi

log "Linking current -> releases/$VERSION..."
ln -sfn "$RELEASE_DIR" "$BASE_DIR/current"
chown -h "$SERVICE_USER":"$SERVICE_USER" "$BASE_DIR/current"

log "Installing systemd service..."
sed \
    -e "s|__RUUVIHUB_BASE__|$BASE_DIR|g" \
    -e "s|__RUUVIHUB_USER__|$SERVICE_USER|g" \
    "$SCRIPT_DIR/ruuvihub.service.template" > /etc/systemd/system/ruuvihub.service

systemctl daemon-reload
systemctl enable ruuvihub.service
systemctl restart ruuvihub.service

if wait_for_healthy "$VERSION"; then
    IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
    HEALTH_URL="$(health_check_url)"
    PUBLIC_URL="${HEALTH_URL/localhost/${IP:-<pi-ip>}}"
    PUBLIC_URL="${PUBLIC_URL%/healthz}"
    log "RuuviHub $VERSION is running. Open $PUBLIC_URL in a browser."
    log "The certificate is self-signed -- your browser will warn once; accept/proceed to continue."
    log "Default login: admin / ruuvihub — change it from the Config page."
else
    warn "Service did not become healthy within the timeout. Check: sudo journalctl -u ruuvihub -n 100"
    exit 1
fi
