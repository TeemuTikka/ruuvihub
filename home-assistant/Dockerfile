FROM python:3.11-slim@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535

ARG BUILD_ARCH
ARG BUILD_VERSION
LABEL io.hass.type="addon" \
      io.hass.arch="${BUILD_ARCH}" \
      io.hass.version="${BUILD_VERSION}" \
      org.opencontainers.image.source="https://github.com/TeemuTikka/ruuvihub"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DBUS_SYSTEM_BUS_ADDRESS=unix:path=/run/dbus/system_bus_socket \
    SETTINGS_PATH=/data/settings.local.json

WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --require-hashes --only-binary=:all: -r requirements.txt

COPY config.json LICENSE THIRD_PARTY_NOTICES.md ./
COPY third_party_licenses ./third_party_licenses
COPY src ./src
RUN <<'EOF'
set -eu
python - <<'PYTHON'
import json
from pathlib import Path

config_path = Path("config.json")
config = json.loads(config_path.read_text(encoding="utf-8"))
config.update(
    db_path="/data/ruuvi_data.db",
    log_path="/data/logs/ruuvihub.log",
    tls_cert_path="/data/certs/ruuvihub.crt",
    tls_key_path="/data/certs/ruuvihub.key",
)
config_path.write_text(json.dumps(config), encoding="utf-8")
PYTHON
printf 'v%s\n' "$BUILD_VERSION" > VERSION
EOF

EXPOSE 8443
STOPSIGNAL SIGINT
CMD ["python", "src/main.py"]
