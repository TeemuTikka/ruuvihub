#!/usr/bin/env bash
# Shared helpers for RuuviHub's install/update/rollback scripts.
# Sourced by scripts/install.sh, scripts/update.sh, scripts/rollback.sh —
# not meant to be run directly.

set -euo pipefail

RUUVIHUB_DEFAULT_BASE_DIR="/home/pi/ruuvihub"
RUUVIHUB_DEFAULT_USER="pi"
RUUVIHUB_DEFAULT_PORT=8080
RUUVIHUB_DEFAULT_HTTPS_PORT=8443
RUUVIHUB_DEFAULT_KEEP=3

log()  { echo "[ruuvihub] $*"; }
warn() { echo "[ruuvihub] WARNING: $*" >&2; }
die()  { echo "[ruuvihub] ERROR: $*" >&2; exit 1; }

# All privileged filesystem/systemd operations expect to run under sudo;
# Python venv/pip operations are dropped to $SERVICE_USER so installed
# files are owned by the service account, not root.
require_root() {
    if [[ $EUID -ne 0 ]]; then
        die "this script must be run as root (e.g. via sudo)."
    fi
}

as_service_user() {
    sudo -u "$SERVICE_USER" -H "$@"
}

# Validates that a version string matches the vX.Y.Z tag format used by
# the release pipeline. The value is used to build filesystem paths
# (releases/<version>, shared/venvs/<version>) and is later passed to
# recursive chown/rm operations while running as root, so an
# unvalidated value (e.g. a crafted VERSION file containing "../..")
# could escape the intended directories. "dev" (the local-checkout
# fallback) is intentionally exempt since it's a fixed literal, never
# read from an archive.
validate_version() {
    local version="$1"
    [[ "$version" == "dev" || "$version" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] \
        || die "invalid version string: '$version' (expected vX.Y.Z)"
}

# Reads the VERSION file at the root of an extracted release directory.
# Falls back to "dev" if missing (shouldn't happen for CI-built archives).
read_version_file() {
    local dir="$1"
    local version
    if [[ -f "$dir/VERSION" ]]; then
        version="$(tr -d '[:space:]' < "$dir/VERSION")"
    else
        version="dev"
    fi
    validate_version "$version"
    echo "$version"
}

# Path to the dedicated virtualenv directory for a given version, under
# shared/venvs/. shared/venv is a symlink pointed at one of these.
venv_dir_for_version() {
    echo "$BASE_DIR/shared/venvs/$1"
}

# Builds a fresh, release-specific virtualenv at shared/venvs/<version>
# and installs the given requirements file into it. Building a new venv
# per version (instead of `pip install`-ing into the one shared venv in
# place) keeps updates/rollbacks atomic: the previously active venv is
# left completely untouched until shared/venv's symlink is flipped, so
# a failed update or rollback can always restore the exact environment
# the previous release was running with.
build_release_venv() {
    local version="$1"
    local requirements_file="$2"
    local venv_dir
    venv_dir="$(venv_dir_for_version "$version")"

    if [[ ! -d "$venv_dir" ]]; then
        as_service_user python3 -m venv "$venv_dir"
    fi
    as_service_user "$venv_dir/bin/pip" install --upgrade pip
    as_service_user "$venv_dir/bin/pip" install -r "$requirements_file"
    chown -R "$SERVICE_USER":"$SERVICE_USER" "$venv_dir"
}

# Atomically points shared/venv at the dedicated venv for $1 (version),
# if one exists. Installs created before per-version venvs existed may
# only have the single legacy shared/venv; in that case this is a no-op
# and the (mutated) shared venv keeps being used as-is.
activate_venv() {
    local version="$1"
    local venv_dir
    venv_dir="$(venv_dir_for_version "$version")"
    if [[ -d "$venv_dir" ]]; then
        ln -sfn "$venv_dir" "$BASE_DIR/shared/venv"
        chown -h "$SERVICE_USER":"$SERVICE_USER" "$BASE_DIR/shared/venv"
    fi
}

# Resolves the effective health-check URL from the *persisted* settings
# (shared/settings.local.json), not a hard-coded scheme/port: honors a
# custom ``https_port`` and correctly checks plain HTTP when
# ``https_enabled`` is false, instead of always querying
# ``https://localhost:<default-https-port>`` regardless of how the
# installation is actually configured (which fails to check a
# `https_enabled: false` setup at all, and ignores a custom port).
# Falls back to the documented defaults (HTTPS on port 8443) if the
# settings file doesn't exist yet (e.g. first boot before the app has
# written it) or can't be parsed.
health_check_url() {
    local settings_file="$BASE_DIR/shared/settings.local.json"
    local https_enabled="true"
    local https_port="$RUUVIHUB_DEFAULT_HTTPS_PORT"

    if [[ -f "$settings_file" ]]; then
        local resolved
        if resolved="$(python3 -c '
import json, sys
try:
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        data = json.load(f)
except Exception:
    data = {}
print("true" if data.get("https_enabled", True) else "false")
print(data.get("https_port", sys.argv[2]))
' "$settings_file" "$RUUVIHUB_DEFAULT_HTTPS_PORT" 2>/dev/null)"; then
            https_enabled="$(sed -n '1p' <<<"$resolved")"
            https_port="$(sed -n '2p' <<<"$resolved")"
        fi
    fi

    if [[ "$https_enabled" == "true" ]]; then
        echo "https://localhost:${https_port}/healthz"
    else
        echo "http://localhost:${RUUVIHUB_DEFAULT_PORT}/healthz"
    fi
}

# Verifies the service is healthy: systemd reports it active AND the
# local /healthz endpoint responds with the expected version (if one is
# given). Retries for a few seconds since uvicorn/BLE init takes a
# moment after (re)start. Checking the reported version (not just HTTP
# 200) catches cases where systemd reports the unit active/healthy but
# it's actually still serving an old/other installation.
#
# Queries whatever scheme/port ``health_check_url`` resolves from the
# persisted settings by default (``-k`` is only added for an https:// URL,
# to skip verifying the self-signed certificate) -- pass an explicit URL
# to override, e.g. to check a specific listener regardless of settings.
wait_for_healthy() {
    local expected_version="${1:-}"
    local url="${2:-$(health_check_url)}"
    local attempts="${3:-10}"
    local delay="${4:-2}"
    local curl_opts=(-sf)
    [[ "$url" == https://* ]] && curl_opts+=(-k)

    for ((i = 1; i <= attempts; i++)); do
        if systemctl is-active --quiet ruuvihub.service; then
            local response
            if response="$(curl "${curl_opts[@]}" "$url" 2>/dev/null)"; then
                if [[ -z "$expected_version" ]]; then
                    return 0
                fi
                local actual_version
                actual_version="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("version",""))' <<<"$response" 2>/dev/null || true)"
                [[ "$actual_version" == "$expected_version" ]] && return 0
            fi
        fi
        sleep "$delay"
    done
    return 1
}

# Version-aware sort (v1.0.9 before v1.0.10) for release directory names.
sort_versions() {
    sort -V
}
