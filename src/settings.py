"""Runtime-editable settings store for RuuviHub's web UI.

Unlike ``src/config.py`` (a static bootstrap/fallback template, normally
tracked in git), this module manages ``settings.local.json`` — a
git-ignored file holding everything the web UI can change at runtime:
scan/upload intervals, tag whitelist & friendly names, retention, cloud
upload connection details, dashboard refresh interval, and the UI's own
login credentials/session secret.

Precedence on startup:
  - If ``settings.local.json`` exists and parses/validates successfully,
    it is loaded as-is and treated as the live source of truth.
  - If it is missing, a fresh ``Settings`` instance is bootstrapped from
    ``config.json``'s defaults (plus a freshly generated session secret
    and the documented default admin account) and written out as the new
    ``settings.local.json``. An existing, valid ``settings.local.json``
    is NEVER overwritten by ``config.json`` on subsequent startups.
  - If it exists but fails to parse/validate (corrupt), the store fails
    closed: the corrupt file is backed up next to the original path and
    ``SettingsCorruptError`` is raised rather than silently
    re-bootstrapping the documented default admin/ruuvihub account over
    what may have been a previously secured installation. An operator
    must explicitly delete/restore the file to continue.

Note on ``db_path`` (and ``log_path``): both are stored here as part of
one consistent settings snapshot, but the web UI must never expose them
as live-editable fields. Changing where the SQLite file or log file
lives requires fully re-initializing the affected subsystem (closing/
reopening the DB, reconfiguring logging handlers), which is out of
scope for live reload — both fields are file-only and require a
service restart to take effect.
"""

import asyncio
import copy
import dataclasses
import hashlib
import json
import logging
import os
import secrets
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from config import Config, load_config
from datetime_formats import DEFAULT_DATETIME_FORMAT, DATETIME_FORMATS
from dashboard_preferences import DEFAULT_DASHBOARD_METRICS, MAX_DASHBOARD_CHANGE_HOURS

logger = logging.getLogger(__name__)


class SettingsCorruptError(RuntimeError):
    """Raised when an existing settings.local.json fails to parse/validate.

    Deliberately fails closed instead of silently bootstrapping the
    documented default admin/ruuvihub account over a previously secured
    installation: a corrupted file is backed up alongside the original
    path and the operator must explicitly delete/restore it to continue.
    """

# Documented first-run credentials. Users are nudged (via a persistent UI
# banner, not a forced flow) to change these on the Account settings page.
DEFAULT_UI_USERNAME = "admin"
DEFAULT_UI_PASSWORD = "ruuvihub"
UPLOAD_DESTINATIONS = ("http", "azure_blob")

_PBKDF2_ITERATIONS = 260_000


def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    """Hash a password using PBKDF2-HMAC-SHA256 (stdlib, no extra dependency).

    Returns a single string ``"<salt_hex>$<digest_hex>"`` suitable for
    storage in ``Settings.ui_password_hash``.
    """
    if salt is None:
        salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Check a plaintext password against a hash produced by ``hash_password``."""
    try:
        salt_hex, _ = stored_hash.split("$", 1)
        salt = bytes.fromhex(salt_hex)
    except (ValueError, AttributeError):
        return False
    candidate = hash_password(password, salt)
    return secrets.compare_digest(candidate, stored_hash)


_POSITIVE_INT_FIELDS = (
    "scan_interval_seconds",
    "scan_timeout_seconds",
    "upload_interval_seconds",
    "max_entries_per_tag",
    "dashboard_refresh_seconds",
    "dashboard_change_hours",
    "history_backfill_interval_seconds",
    "history_backfill_failure_threshold",
    "history_backfill_timeout_seconds",
)
_PORT_FIELDS = ("https_port",)
_NON_NEGATIVE_INT_FIELDS = ("db_retention_days",)
_STRING_LIST_FIELDS = ("tag_addresses", "dashboard_hidden_tags", "dashboard_visible_metrics")
_STRING_DICT_FIELDS = ("tag_names", "history_backfill_disabled_tags")
_INT_DICT_FIELDS = ("history_backfill_failure_counts",)
_STRING_FIELDS = (
    "db_path",
    "log_path",
    "cloud_endpoint_url",
    "cloud_upload_destination",
    "azure_blob_container_url",
    "cloud_auth_header_name",
    "cloud_auth_header_value",
    "ui_username",
    "ui_password_hash",
    "session_secret",
    "tls_cert_path",
    "tls_key_path",
)
_BOOL_FIELDS = (
    "scan_enabled",
    "cloud_upload_enabled",
    "dashboard_show_storage_info",
    "history_backfill_enabled",
    "history_backfill_on_startup",
    "history_backfill_periodic",
    "https_enabled",
    "https_redirect_http",
)


def validate_fields(data: dict) -> dict:
    """Validate/coerce a raw (already key-filtered) settings dict.

    ``settings.local.json`` is documented as hand-editable, so merely
    being valid JSON with known keys is not enough: wrong types or
    out-of-range values (e.g. a zero/negative interval, ``tag_names`` as
    a list) must not silently reach ``Settings.__init__`` where they'd
    later break page rendering or create a tight scan/upload loop.
    Invalid/out-of-range values are dropped (falling back to the
    dataclass default) rather than raising, so one bad field doesn't
    take down the whole file.
    """
    validated = dict(data)
    if "dashboard_visible_metrics" in validated:
        metrics = validated["dashboard_visible_metrics"]
        if not isinstance(metrics, list) or any(key not in DEFAULT_DASHBOARD_METRICS for key in metrics):
            logger.warning("Ignoring invalid dashboard metrics")
            validated.pop("dashboard_visible_metrics")
    if "dashboard_change_hours" in validated:
        hours = validated["dashboard_change_hours"]
        if type(hours) is not int or not 1 <= hours <= MAX_DASHBOARD_CHANGE_HOURS:
            logger.warning("Ignoring invalid dashboard comparison period")
            validated.pop("dashboard_change_hours")
    if "datetime_format" in validated and validated["datetime_format"] not in DATETIME_FORMATS:
        logger.warning("Ignoring invalid datetime_format; expected fi or iso")
        validated.pop("datetime_format")

    if "cloud_upload_destination" in validated and validated["cloud_upload_destination"] not in UPLOAD_DESTINATIONS:
        logger.warning("Ignoring invalid upload destination")
        validated.pop("cloud_upload_destination")

    for name in _POSITIVE_INT_FIELDS:
        if name in validated:
            value = validated[name]
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                logger.warning("Ignoring invalid settings field %s=%r (must be a positive integer)", name, value)
                validated.pop(name)

    for name in _PORT_FIELDS:
        if name in validated:
            value = validated[name]
            if not isinstance(value, int) or isinstance(value, bool) or not (1 <= value <= 65535):
                logger.warning("Ignoring invalid settings field %s=%r (must be a TCP port 1-65535)", name, value)
                validated.pop(name)

    for name in _NON_NEGATIVE_INT_FIELDS:
        if name in validated:
            value = validated[name]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                logger.warning("Ignoring invalid settings field %s=%r (must be a non-negative integer)", name, value)
                validated.pop(name)

    for name in _STRING_LIST_FIELDS:
        if name in validated:
            value = validated[name]
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                logger.warning("Ignoring invalid settings field %s=%r (must be a list of strings)", name, value)
                validated.pop(name)

    for name in _STRING_DICT_FIELDS:
        if name in validated:
            value = validated[name]
            if not isinstance(value, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in value.items()
            ):
                logger.warning("Ignoring invalid settings field %s=%r (must be a string-to-string mapping)", name, value)
                validated.pop(name)

    for name in _INT_DICT_FIELDS:
        if name in validated:
            value = validated[name]
            if not isinstance(value, dict) or not all(
                isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool)
                for k, v in value.items()
            ):
                logger.warning("Ignoring invalid settings field %s=%r (must be a string-to-int mapping)", name, value)
                validated.pop(name)

    for name in _STRING_FIELDS:
        if name in validated and not isinstance(validated[name], str):
            logger.warning("Ignoring invalid settings field %s=%r (must be a string)", name, validated[name])
            validated.pop(name)

    for name in _BOOL_FIELDS:
        if name in validated and not isinstance(validated[name], bool):
            logger.warning(
                "Ignoring invalid settings field %s=%r (must be a boolean)",
                name,
                validated[name],
            )
            validated.pop(name)

    return validated


@dataclass
class Settings:
    """All runtime-editable RuuviHub settings.

    Fields mirror ``config.py``'s ``Config`` plus everything the web UI
    additionally manages (cloud upload auth, dashboard refresh, tag
    friendly names, and the UI's own login/session secret).
    """

    datetime_format: str = DEFAULT_DATETIME_FORMAT  # file-only; takes effect after restart
    scan_enabled: bool = True
    scan_interval_seconds: int = 60
    scan_timeout_seconds: int = 5
    upload_interval_seconds: int = 300
    max_entries_per_tag: int = 100
    tag_addresses: List[str] = field(default_factory=list)
    db_path: str = ""  # file-only; excluded from the live-editable web UI
    db_retention_days: int = 0
    log_path: str = ""  # file-only; excluded from the live-editable web UI (like db_path)

    # Cloud upload: a single flexible header name/value pair rather than a
    # fixed Bearer-token scheme, so it works with Azure Functions
    # (`x-functions-key`), Cloud Run (`Authorization: Bearer ...`), or any
    # other webhook without RuuviHub assuming a specific auth scheme.
    # Disabled by default -- the user must explicitly opt in via the
    # config page even after an endpoint/credentials are configured.
    cloud_upload_enabled: bool = False
    cloud_upload_destination: str = "http"
    cloud_endpoint_url: str = ""
    azure_blob_container_url: str = ""
    cloud_auth_header_name: str = ""
    cloud_auth_header_value: str = ""

    dashboard_refresh_seconds: int = 300
    dashboard_show_storage_info: bool = True
    dashboard_change_hours: int = 1
    dashboard_visible_metrics: List[str] = field(default_factory=lambda: DEFAULT_DASHBOARD_METRICS.copy())
    dashboard_hidden_tags: List[str] = field(default_factory=list)
    tag_names: Dict[str, str] = field(default_factory=dict)

    # GATT history backfill -- enabled + run-once-on-startup by default so
    # downtime recovery (the original "load missing data" goal) works out
    # of the box; periodic re-sync stays opt-in since it repeatedly steals
    # the BLE radio from scan_loop (see README-dev.md's backfill section).
    # history_backfill_disabled_tags maps mac -> a short reason string, set
    # automatically once a tag hits history_backfill_failure_threshold
    # consecutive failures; an admin clears an entry from the Config page
    # to re-enable that tag for backfill.
    history_backfill_enabled: bool = True
    history_backfill_on_startup: bool = True
    history_backfill_periodic: bool = False
    history_backfill_interval_seconds: int = 3600
    history_backfill_failure_threshold: int = 5
    history_backfill_disabled_tags: Dict[str, str] = field(default_factory=dict)
    # Per-tag GATT history pull timeout, separate from scan_timeout_seconds
    # (which only bounds a quick passive-advertisement scan window). A
    # history pull can legitimately take much longer -- a tag holding
    # close to its full ~12-day on-device log depth can take a minute or
    # two to download over GATT -- so this needs its own, larger budget
    # rather than sharing scan_timeout_seconds's much shorter default.
    history_backfill_timeout_seconds: int = 120
    # Persisted per-mac consecutive-failure counters so a restarted process
    # doesn't reset partial failure counts and retry permanently broken tags forever.
    history_backfill_failure_counts: Dict[str, int] = field(default_factory=dict)

    # HTTPS (on by default). All five fields below are file-only (like
    # db_path/log_path): none are exposed on the Config page's form
    # because every one of them requires a service restart to take
    # effect (the TLS listener/redirect server can't be hot-swapped), so
    # they're edited directly in settings.local.json instead.
    # tls_cert_path/tls_key_path: empty means "auto-generate/use a
    # self-signed cert at certs/ruuvihub.{crt,key} next to main.py".
    https_enabled: bool = True
    https_port: int = 8443
    tls_cert_path: str = ""
    tls_key_path: str = ""
    https_redirect_http: bool = True

    ui_username: str = DEFAULT_UI_USERNAME
    ui_password_hash: str = ""
    session_secret: str = ""

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Settings":
        """Build a Settings instance from a dict, ignoring unknown keys.

        Ignoring unknown keys keeps loading forward-compatible if fields
        are ever renamed/removed between versions. Values are validated
        and coerced (see ``validate_fields``) since ``settings.local.json``
        is documented as hand-editable -- a merely-well-formed-JSON file
        is not necessarily a *valid* one.
        """
        field_names = {f.name for f in dataclasses.fields(cls)}
        filtered = {k: v for k, v in data.items() if k in field_names}
        filtered = validate_fields(filtered)
        return cls(**filtered)

    def is_using_default_credentials(self) -> bool:
        """True if the account still has the documented default login.

        Used to show a persistent "change your password" reminder banner
        in the UI (no forced password-change flow).
        """
        return self.ui_username == DEFAULT_UI_USERNAME and verify_password(
            DEFAULT_UI_PASSWORD, self.ui_password_hash
        )

    def verify_login(self, username: str, password: str) -> bool:
        """Check a submitted username/password against the stored account."""
        return secrets.compare_digest(
            username, self.ui_username
        ) and verify_password(password, self.ui_password_hash)


def _settings_from_config(config: Config) -> Settings:
    """Build a fresh Settings baseline from a loaded config.json Config."""
    return Settings(
        datetime_format=config.datetime_format,
        scan_enabled=config.scan_enabled,
        scan_interval_seconds=config.scan_interval_seconds,
        scan_timeout_seconds=config.scan_timeout_seconds,
        upload_interval_seconds=config.upload_interval_seconds,
        max_entries_per_tag=config.max_entries_per_tag,
        tag_addresses=list(config.tag_addresses),
        db_path=config.db_path,
        db_retention_days=config.db_retention_days,
        log_path=config.log_path,
        history_backfill_enabled=config.history_backfill_enabled,
        history_backfill_on_startup=config.history_backfill_on_startup,
        history_backfill_periodic=config.history_backfill_periodic,
        history_backfill_interval_seconds=config.history_backfill_interval_seconds,
        history_backfill_failure_threshold=config.history_backfill_failure_threshold,
        history_backfill_timeout_seconds=config.history_backfill_timeout_seconds,
        https_enabled=config.https_enabled,
        https_port=config.https_port,
        tls_cert_path=config.tls_cert_path,
        tls_key_path=config.tls_key_path,
        https_redirect_http=config.https_redirect_http,
        ui_username=DEFAULT_UI_USERNAME,
        ui_password_hash=hash_password(DEFAULT_UI_PASSWORD),
        session_secret=secrets.token_hex(32),
    )


class SettingsStore:
    """Async-safe load/save wrapper around ``settings.local.json``."""

    def __init__(
        self,
        settings_path: Optional[str] = None,
        config_path: Optional[str] = None,
    ) -> None:
        if settings_path is None:
            settings_path = os.environ.get(
                "SETTINGS_PATH",
                str(Path(__file__).parent.parent / "settings.local.json"),
            )
        self._settings_path = settings_path
        self._config_path = config_path
        self._lock = asyncio.Lock()
        self._settings = self._load_or_bootstrap()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get(self) -> Settings:
        """Return a deep-copy snapshot of the current settings.

        A deep copy (not ``dataclasses.replace``, which only shallow-copies
        top-level fields) so mutable fields like ``tag_addresses``/
        ``tag_names`` aren't shared with the live store -- callers can't
        accidentally mutate live settings without the lock or persistence.
        """
        async with self._lock:
            return copy.deepcopy(self._settings)

    def get_sync(self) -> Settings:
        """Synchronous deep-copy snapshot accessor (e.g. before the event loop starts)."""
        return copy.deepcopy(self._settings)

    async def update(self, **fields) -> Settings:
        """Update one or more fields, persist to disk, and return the new snapshot.

        Unknown field names are ignored (logged) so callers can pass
        partial/loosely-validated form data without crashing the app.
        Values are validated/coerced the same way as on load (see
        ``validate_fields``) since callers include HTTP form data.
        """
        async with self._lock:
            valid_names = {f.name for f in dataclasses.fields(Settings)}
            known_fields = {k: v for k, v in fields.items() if k in valid_names}
            for key in fields:
                if key not in valid_names:
                    logger.warning("Ignoring unknown settings field: %s", key)
            validated_fields = validate_fields(known_fields)
            for key, value in validated_fields.items():
                setattr(self._settings, key, value)
            self._write(self._settings)
            return copy.deepcopy(self._settings)


    async def set_backfill_failure_count(self, mac: str, count: Optional[int]) -> Settings:
        """Atomically set or clear a single tag's persisted consecutive-failure count."""
        async with self._lock:
            counts = dict(self._settings.history_backfill_failure_counts)
            if count is not None and count > 0:
                counts[mac] = count
            else:
                counts.pop(mac, None)
            self._settings.history_backfill_failure_counts = counts
            self._write(self._settings)
            return copy.deepcopy(self._settings)

    async def set_backfill_disabled(self, mac: str, reason: Optional[str]) -> Settings:
        """Atomically disable (set a reason) or re-enable (clear) history
        backfill for a single tag.

        Mirrors ``set_backfill_failure_count()``'s single-mac read/mutate/
        persist pattern under one lock acquisition, rather than round-tripping the
        whole ``history_backfill_disabled_tags`` dict through the caller
        (racy under concurrent updates for *different* tags -- e.g. the
        backfill loop auto-disabling one tag for repeated failures at the
        same time an admin re-enables another from the Config page).

        Passing ``reason=None`` (or an empty/whitespace string) re-enables
        the tag by removing it from the disabled map.
        """
        async with self._lock:
            disabled = dict(self._settings.history_backfill_disabled_tags)
            stripped = (reason or "").strip()
            if stripped:
                disabled[mac] = stripped
            else:
                disabled.pop(mac, None)
            self._settings.history_backfill_disabled_tags = disabled
            self._write(self._settings)
            return copy.deepcopy(self._settings)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_or_bootstrap(self) -> Settings:
        try:
            with open(self._settings_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            settings = Settings.from_dict(data)
            logger.info("Loaded settings from %s", self._settings_path)
            return settings
        except FileNotFoundError:
            logger.info(
                "No settings file found at %s – bootstrapping from config.json",
                self._settings_path,
            )
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            backup_path = f"{self._settings_path}.corrupt-{int(time.time())}"
            try:
                shutil.copy2(self._settings_path, backup_path)
                logger.critical(
                    "settings.local.json is corrupt – backed up to %s. "
                    "Refusing to auto-bootstrap default admin credentials "
                    "over what may be a previously secured installation. "
                    "Restore a good backup, or delete the corrupt file to "
                    "explicitly accept fresh defaults, then restart.",
                    backup_path,
                )
            except OSError:
                logger.critical(
                    "settings.local.json is corrupt and could not be backed up. "
                    "Refusing to auto-bootstrap default admin credentials."
                )
            raise SettingsCorruptError(
                f"Corrupt settings file at {self._settings_path}"
            ) from exc

        config = load_config(self._config_path)
        settings = _settings_from_config(config)
        self._write(settings)
        return settings

    def _write(self, settings: Settings) -> None:
        """Atomically write settings to disk (temp file + rename).

        On POSIX, the temp file has ``0600`` permissions from creation,
        before any secrets are written. Windows uses the parent folder's
        ACL. A failed write leaves the previous file intact.
        """
        path = Path(self._settings_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(settings.to_dict(), f, indent=2)
            os.replace(tmp_path, path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)
