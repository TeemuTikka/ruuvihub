"""Configuration loader for RuuviHub."""

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List
from datetime_formats import DEFAULT_DATETIME_FORMAT, DATETIME_FORMATS

logger = logging.getLogger(__name__)



@dataclass
class Config:
    datetime_format: str = DEFAULT_DATETIME_FORMAT
    scan_enabled: bool = True
    scan_interval_seconds: int = 60
    scan_timeout_seconds: int = 5
    upload_interval_seconds: int = 300
    max_entries_per_tag: int = 100
    tag_addresses: List[str] = field(default_factory=list)
    db_path: str = ""  # empty = default (ruuvi_data.db next to main.py)
    db_retention_days: int = 0  # 0 = keep readings forever (no pruning)
    log_path: str = ""  # empty = default (logs/ruuvihub.log next to main.py, one file per day); file-only, requires restart

    # GATT history backfill -- enabled + run-once-on-startup by default so
    # downtime recovery works out of the box; periodic re-sync stays
    # opt-in (see README-dev.md's GATT history backfill section).
    history_backfill_enabled: bool = True
    history_backfill_on_startup: bool = True
    history_backfill_periodic: bool = False
    history_backfill_interval_seconds: int = 3600
    history_backfill_failure_threshold: int = 5
    # Per-tag GATT history pull timeout -- separate from
    # scan_timeout_seconds (which only bounds a quick passive-scan
    # window). Pulling a tag's on-device history log can legitimately
    # take a minute or two, so this needs a much larger, independently
    # configurable budget.
    history_backfill_timeout_seconds: int = 120

    # HTTPS (on by default -- see docs/README.md; a self-signed cert is
    # auto-generated on first run if tls_cert_path/tls_key_path are empty)
    https_enabled: bool = True
    https_port: int = 8443
    tls_cert_path: str = ""  # empty = default (certs/ruuvihub.crt next to main.py)
    tls_key_path: str = ""  # empty = default (certs/ruuvihub.key next to main.py)
    https_redirect_http: bool = True  # serve a 301 redirect on the plain-HTTP port


def _validated_bool(data: dict, key: str, default: bool) -> bool:
    """Return ``data[key]`` only if it is actually a bool, else ``default``.

    ``bool("false")`` and ``bool("")`` don't mean what a config-file author
    would expect (a non-empty string like ``"false"`` is truthy in Python),
    so a raw ``bool(data.get(key, default))`` cast can silently flip an
    opt-in feature like ``history_backfill_periodic`` on from a JSON string
    value. Mirrors the strict validation ``settings.validate_fields``
    applies to the same fields once loaded into a live ``SettingsStore``.
    """
    value = data.get(key, default)
    if not isinstance(value, bool):
        logger.warning("Ignoring invalid config field %s=%r (must be a boolean); using default %r", key, value, default)
        return default
    return value


def _validated_positive_int(data: dict, key: str, default: int) -> int:
    """Return ``data[key]`` only if it is a positive int, else ``default``.

    Without this, a zero/negative interval (e.g.
    ``history_backfill_interval_seconds``) reaches ``asyncio.sleep()``
    downstream and can create a tight, CPU-spinning loop. Mirrors
    ``settings.validate_fields``'s handling of the same fields.
    """
    value = data.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        logger.warning("Ignoring invalid config field %s=%r (must be a positive integer); using default %r", key, value, default)
        return default
    return value


def _validated_port(data: dict, key: str, default: int) -> int:
    """Return ``data[key]`` only if it is a valid TCP port (1-65535).

    A generic positive-integer check accepts out-of-range values like
    65536, which uvicorn/socket.bind() then reject at runtime, crashing
    the web-server loop on every retry.
    """
    value = data.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or not (1 <= value <= 65535):
        logger.warning("Ignoring invalid config field %s=%r (must be a TCP port 1-65535); using default %r", key, value, default)
        return default
    return value


def _validated_non_negative_int(data: dict, key: str, default: int) -> int:
    """Return ``data[key]`` only if it is a non-negative int, else ``default``."""
    value = data.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        logger.warning("Ignoring invalid config field %s=%r (must be a non-negative integer); using default %r", key, value, default)
        return default
    return value


def load_config(path: str | None = None) -> Config:
    """Load configuration from a JSON file.

    Args:
        path: Path to config file. Defaults to CONFIG_PATH env var or
              config.json next to this file's parent directory.
    """
    if path is None:
        path = os.environ.get(
            "CONFIG_PATH",
            str(Path(__file__).parent.parent / "config.json"),
        )

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    datetime_format = data.get("datetime_format", DEFAULT_DATETIME_FORMAT)
    if datetime_format not in DATETIME_FORMATS:
        logger.warning("Ignoring invalid datetime_format; expected fi or iso")
        datetime_format = DEFAULT_DATETIME_FORMAT
    return Config(
        datetime_format=datetime_format,
        scan_enabled=_validated_bool(data, "scan_enabled", True),
        scan_interval_seconds=_validated_positive_int(data, "scan_interval_seconds", 60),
        scan_timeout_seconds=_validated_positive_int(data, "scan_timeout_seconds", 5),
        upload_interval_seconds=_validated_positive_int(data, "upload_interval_seconds", 300),
        max_entries_per_tag=_validated_positive_int(data, "max_entries_per_tag", 100),
        tag_addresses=list(data.get("tag_addresses", [])),
        db_path=str(data.get("db_path", "")),
        db_retention_days=_validated_non_negative_int(data, "db_retention_days", 0),
        log_path=str(data.get("log_path", "")),
        history_backfill_enabled=_validated_bool(data, "history_backfill_enabled", True),
        history_backfill_on_startup=_validated_bool(data, "history_backfill_on_startup", True),
        history_backfill_periodic=_validated_bool(data, "history_backfill_periodic", False),
        history_backfill_interval_seconds=_validated_positive_int(data, "history_backfill_interval_seconds", 3600),
        history_backfill_failure_threshold=_validated_positive_int(data, "history_backfill_failure_threshold", 5),
        history_backfill_timeout_seconds=_validated_positive_int(data, "history_backfill_timeout_seconds", 120),
        https_enabled=_validated_bool(data, "https_enabled", True),
        https_port=_validated_port(data, "https_port", 8443),
        tls_cert_path=str(data.get("tls_cert_path", "")),
        tls_key_path=str(data.get("tls_key_path", "")),
        https_redirect_http=_validated_bool(data, "https_redirect_http", True),
    )
