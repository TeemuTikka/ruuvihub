"""Tests for src/settings.py."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from settings import (
    DEFAULT_UI_PASSWORD,
    DEFAULT_UI_USERNAME,
    Settings,
    SettingsCorruptError,
    SettingsStore,
    hash_password,
    verify_password,
)


def write_config(tmp_path, data: dict) -> str:
    path = os.path.join(tmp_path, "config.json")
    with open(path, "w") as f:
        json.dump(data, f)
    return path


# ----------------------------------------------------------------------
# Password hashing
# ----------------------------------------------------------------------

def test_hash_password_round_trip():
    hashed = hash_password("hunter2")
    assert verify_password("hunter2", hashed) is True


def test_hash_password_rejects_wrong_password():
    hashed = hash_password("hunter2")
    assert verify_password("wrong-password", hashed) is False


def test_hash_password_uses_random_salt_by_default():
    h1 = hash_password("same-password")
    h2 = hash_password("same-password")
    assert h1 != h2  # different salts -> different stored hash strings
    assert verify_password("same-password", h1)
    assert verify_password("same-password", h2)


def test_verify_password_handles_malformed_hash_gracefully():
    assert verify_password("anything", "not-a-valid-hash") is False


# ----------------------------------------------------------------------
# Settings dataclass helpers
# ----------------------------------------------------------------------

def test_settings_from_dict_ignores_unknown_keys():
    settings = Settings.from_dict({"scan_interval_seconds": 42, "bogus_field": "x"})
    assert settings.scan_interval_seconds == 42
    assert not hasattr(settings, "bogus_field")


def test_settings_is_using_default_credentials():
    settings = Settings(
        ui_username=DEFAULT_UI_USERNAME,
        ui_password_hash=hash_password(DEFAULT_UI_PASSWORD),
    )
    assert settings.is_using_default_credentials() is True

    settings.ui_password_hash = hash_password("something-else")
    assert settings.is_using_default_credentials() is False


def test_settings_verify_login():
    settings = Settings(ui_username="alice", ui_password_hash=hash_password("secret"))
    assert settings.verify_login("alice", "secret") is True
    assert settings.verify_login("alice", "wrong") is False
    assert settings.verify_login("bob", "secret") is False


def test_https_settings_defaults():
    settings = Settings()
    assert settings.https_enabled is True
    assert settings.https_port == 8443
    assert settings.tls_cert_path == ""
    assert settings.tls_key_path == ""
    assert settings.https_redirect_http is True


def test_dashboard_refresh_defaults_to_five_minutes():
    settings = Settings()
    assert settings.dashboard_refresh_seconds == 300
    assert settings.dashboard_show_storage_info is True


def test_https_settings_validated_on_load(tmp_path):
    config_path = write_config(tmp_path, {})
    settings_path = tmp_path / "settings.local.json"
    settings_path.write_text(
        json.dumps(
            {
                "https_enabled": "yes",  # invalid: must be bool
                "https_port": -1,  # invalid: must be positive
                "tls_cert_path": "/etc/ruuvihub/cert.pem",
                "https_redirect_http": False,
            }
        )
    )

    store = SettingsStore(settings_path=str(settings_path), config_path=config_path)
    settings = store.get_sync()

    assert settings.https_enabled is True  # falls back to dataclass default
    assert settings.https_port == 8443  # falls back to dataclass default
    assert settings.tls_cert_path == "/etc/ruuvihub/cert.pem"
    assert settings.https_redirect_http is False


def test_https_port_rejects_out_of_range_value(tmp_path):
    config_path = write_config(tmp_path, {})
    settings_path = tmp_path / "settings.local.json"
    settings_path.write_text(json.dumps({"https_port": 65536}))

    store = SettingsStore(settings_path=str(settings_path), config_path=config_path)
    settings = store.get_sync()

    assert settings.https_port == 8443  # falls back to dataclass default


# ----------------------------------------------------------------------
# SettingsStore: bootstrap / load / save
# ----------------------------------------------------------------------

def test_bootstraps_from_config_when_settings_file_missing(tmp_path):
    config_path = write_config(
        tmp_path,
        {
            "scan_enabled": False,
            "scan_interval_seconds": 15,
            "tag_addresses": ["AA:BB:CC:DD:EE:01"],
            "log_path": "/tmp/x.log",
        },
    )
    settings_path = str(tmp_path / "settings.local.json")

    store = SettingsStore(settings_path=settings_path, config_path=config_path)

    assert os.path.exists(settings_path)
    settings = store.get_sync()
    assert settings.scan_enabled is False
    assert settings.scan_interval_seconds == 15
    assert settings.tag_addresses == ["AA:BB:CC:DD:EE:01"]
    assert settings.log_path == "/tmp/x.log"
    assert settings.ui_username == DEFAULT_UI_USERNAME
    assert verify_password(DEFAULT_UI_PASSWORD, settings.ui_password_hash)
    assert settings.session_secret  # generated, non-empty


def test_fails_closed_when_settings_file_corrupt(tmp_path):
    """A corrupt settings.local.json must never silently reset to the
    documented default admin/ruuvihub account over a previously secured
    installation -- it should fail closed and back up the bad file."""
    config_path = write_config(tmp_path, {"scan_interval_seconds": 99})
    settings_path = tmp_path / "settings.local.json"
    settings_path.write_text("{ this is not valid json ")

    with pytest.raises(SettingsCorruptError):
        SettingsStore(settings_path=str(settings_path), config_path=config_path)

    # Retain the bad file so systemd retries cannot bootstrap defaults.
    assert settings_path.read_text() == "{ this is not valid json "
    for _ in range(2):
        with pytest.raises(SettingsCorruptError):
            SettingsStore(settings_path=str(settings_path), config_path=config_path)
    backups = list(tmp_path.glob("settings.local.json.corrupt-*"))
    # Backup names include the current second, so retries can create more
    # than one. Every backup must preserve the corrupt file unchanged.
    assert backups
    assert all(backup.read_text() == "{ this is not valid json " for backup in backups)


def test_validates_and_drops_invalid_fields_on_load(tmp_path):
    config_path = write_config(tmp_path, {"scan_interval_seconds": 99})
    settings_path = tmp_path / "settings.local.json"
    settings_path.write_text(
        json.dumps(
            {
                "scan_enabled": "yes",  # invalid: must be bool
                "scan_interval_seconds": 0,  # invalid: must be positive
                "tag_names": [],  # invalid: must be a dict
                "dashboard_refresh_seconds": 7,  # valid
            }
        )
    )

    store = SettingsStore(settings_path=str(settings_path), config_path=config_path)
    settings = store.get_sync()

    assert settings.scan_enabled is True  # falls back to dataclass default
    assert settings.scan_interval_seconds == 60  # falls back to the dataclass default
    assert settings.tag_names == {}
    assert settings.dashboard_refresh_seconds == 7


@pytest.mark.asyncio
async def test_update_ignores_out_of_range_values(tmp_path):
    config_path = write_config(tmp_path, {})
    settings_path = str(tmp_path / "settings.local.json")
    store = SettingsStore(settings_path=settings_path, config_path=config_path)

    updated = await store.update(scan_interval_seconds=-5, dashboard_refresh_seconds=5)
    assert updated.scan_interval_seconds == 60  # unchanged, default preserved
    assert updated.dashboard_refresh_seconds == 5


@pytest.mark.asyncio
async def test_set_backfill_disabled_is_atomic_and_preserves_other_tags(tmp_path):
    config_path = write_config(tmp_path, {})
    settings_path = str(tmp_path / "settings.local.json")
    store = SettingsStore(settings_path=settings_path, config_path=config_path)
    await store.update(history_backfill_disabled_tags={"AA:BB:CC:DD:EE:02": "too far away"})

    await store.set_backfill_disabled("AA:BB:CC:DD:EE:01", "5 consecutive backfill failures")
    settings = await store.get()
    assert settings.history_backfill_disabled_tags == {
        "AA:BB:CC:DD:EE:01": "5 consecutive backfill failures",
        "AA:BB:CC:DD:EE:02": "too far away",
    }

    # Re-enabling (clearing the reason) only removes that one tag.
    await store.set_backfill_disabled("AA:BB:CC:DD:EE:01", None)
    settings = await store.get()
    assert settings.history_backfill_disabled_tags == {"AA:BB:CC:DD:EE:02": "too far away"}


@pytest.mark.asyncio
async def test_history_backfill_settings_defaults(tmp_path):
    config_path = write_config(tmp_path, {})
    settings_path = str(tmp_path / "settings.local.json")
    store = SettingsStore(settings_path=settings_path, config_path=config_path)
    settings = await store.get()

    assert settings.history_backfill_enabled is True
    assert settings.history_backfill_on_startup is True
    assert settings.history_backfill_periodic is False
    assert settings.history_backfill_interval_seconds == 3600
    assert settings.history_backfill_failure_threshold == 5
    assert settings.history_backfill_timeout_seconds == 120
    assert settings.history_backfill_disabled_tags == {}


@pytest.mark.asyncio
async def test_update_rejects_invalid_history_backfill_fields(tmp_path):
    config_path = write_config(tmp_path, {})
    settings_path = str(tmp_path / "settings.local.json")
    store = SettingsStore(settings_path=settings_path, config_path=config_path)

    updated = await store.update(
        history_backfill_enabled="not-a-bool",
        history_backfill_interval_seconds=-5,
        history_backfill_failure_threshold=0,
        history_backfill_timeout_seconds=-10,
        history_backfill_disabled_tags=["not", "a", "dict"],
    )
    assert updated.history_backfill_enabled is True
    assert updated.history_backfill_interval_seconds == 3600
    assert updated.history_backfill_failure_threshold == 5
    assert updated.history_backfill_timeout_seconds == 120
    assert updated.history_backfill_disabled_tags == {}


def test_does_not_overwrite_valid_existing_settings_file(tmp_path):
    config_path = write_config(tmp_path, {"scan_interval_seconds": 99})
    settings_path = str(tmp_path / "settings.local.json")

    # First store bootstraps from config.json.
    SettingsStore(settings_path=settings_path, config_path=config_path)

    # Change config.json afterwards -- a *second* store instance must NOT
    # pick up the new config.json value, since settings.local.json is
    # already valid and is the live source of truth.
    write_config(tmp_path, {"scan_interval_seconds": 1})
    store2 = SettingsStore(settings_path=settings_path, config_path=config_path)

    assert store2.get_sync().scan_interval_seconds == 99


@pytest.mark.asyncio
async def test_update_persists_and_is_reflected_in_get(tmp_path):
    config_path = write_config(tmp_path, {})
    settings_path = str(tmp_path / "settings.local.json")
    store = SettingsStore(settings_path=settings_path, config_path=config_path)

    updated = await store.update(scan_interval_seconds=45, dashboard_refresh_seconds=5)
    assert updated.scan_interval_seconds == 45
    assert updated.dashboard_refresh_seconds == 5

    fetched = await store.get()
    assert fetched.scan_interval_seconds == 45
    assert fetched.dashboard_refresh_seconds == 5

    # A brand-new store instance reading the same file sees the update too.
    store2 = SettingsStore(settings_path=settings_path, config_path=config_path)
    assert store2.get_sync().scan_interval_seconds == 45


@pytest.mark.asyncio
async def test_update_ignores_unknown_fields(tmp_path):
    config_path = write_config(tmp_path, {})
    settings_path = str(tmp_path / "settings.local.json")
    store = SettingsStore(settings_path=settings_path, config_path=config_path)

    updated = await store.update(totally_bogus_field="whatever", scan_interval_seconds=30)
    assert updated.scan_interval_seconds == 30
    assert not hasattr(updated, "totally_bogus_field")


@pytest.mark.asyncio
async def test_get_returns_independent_snapshot(tmp_path):
    """Mutating a returned snapshot must not affect the store's internal state."""
    config_path = write_config(tmp_path, {})
    settings_path = str(tmp_path / "settings.local.json")
    store = SettingsStore(settings_path=settings_path, config_path=config_path)

    snapshot = await store.get()
    snapshot.scan_interval_seconds = 99999

    fresh = await store.get()
    assert fresh.scan_interval_seconds != 99999


@pytest.mark.asyncio
async def test_get_snapshot_is_deep_copied(tmp_path):
    """Mutating a snapshot's mutable fields (list/dict) must not leak back
    into the live store -- dataclasses.replace only shallow-copies."""
    config_path = write_config(tmp_path, {})
    settings_path = str(tmp_path / "settings.local.json")
    store = SettingsStore(settings_path=settings_path, config_path=config_path)
    await store.update(tag_addresses=["AA:BB:CC:DD:EE:01"], tag_names={"AA:BB:CC:DD:EE:01": "Kitchen"})

    snapshot = await store.get()
    snapshot.tag_addresses.append("FF:FF:FF:FF:FF:FF")
    snapshot.tag_names["AA:BB:CC:DD:EE:01"] = "Tampered"

    fresh = await store.get()
    assert fresh.tag_addresses == ["AA:BB:CC:DD:EE:01"]
    assert fresh.tag_names == {"AA:BB:CC:DD:EE:01": "Kitchen"}


@pytest.mark.asyncio
async def test_tag_names_round_trip(tmp_path):
    config_path = write_config(tmp_path, {})
    settings_path = str(tmp_path / "settings.local.json")
    store = SettingsStore(settings_path=settings_path, config_path=config_path)

    await store.update(tag_names={"AA:BB:CC:DD:EE:01": "Living Room"})
    fetched = await store.get()
    assert fetched.tag_names == {"AA:BB:CC:DD:EE:01": "Living Room"}


@pytest.mark.skipif(os.name == "nt", reason="POSIX file permissions not applicable on Windows")
def test_settings_file_is_not_world_readable(tmp_path):
    config_path = write_config(tmp_path, {})
    settings_path = tmp_path / "settings.local.json"

    SettingsStore(settings_path=str(settings_path), config_path=config_path)

    mode = settings_path.stat().st_mode & 0o777
    assert mode == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX file permissions not applicable on Windows")
def test_settings_are_private_before_serialization(tmp_path, monkeypatch):
    config_path = write_config(tmp_path, {})
    original_dump = json.dump
    observed_modes = []

    def check_permissions(data, file, **kwargs):
        observed_modes.append(os.fstat(file.fileno()).st_mode & 0o777)
        return original_dump(data, file, **kwargs)

    monkeypatch.setattr(json, "dump", check_permissions)
    previous_umask = os.umask(0)
    try:
        SettingsStore(settings_path=str(tmp_path / "settings.local.json"), config_path=config_path)
    finally:
        os.umask(previous_umask)
    assert observed_modes == [0o600]


@pytest.mark.asyncio
async def test_failed_settings_write_preserves_file_and_removes_temporary_file(tmp_path, monkeypatch):
    config_path = write_config(tmp_path, {})
    path = tmp_path / "settings.local.json"
    store = SettingsStore(settings_path=str(path), config_path=config_path)
    previous_contents = path.read_bytes()
    previous_files = set(tmp_path.iterdir())

    def fail_during_write(data, file, **kwargs):
        file.write('{"partial":')
        raise OSError("simulated disk write failure")

    monkeypatch.setattr(json, "dump", fail_during_write)
    with pytest.raises(OSError, match="simulated disk write failure"):
        await store.update(scan_interval_seconds=42)
    assert path.read_bytes() == previous_contents
    assert set(tmp_path.iterdir()) == previous_files


def test_upload_destination_defaults_and_validation():
    assert Settings.from_dict({"cloud_endpoint_url":"https://example.com/ingest"}).cloud_upload_destination == "http"
    assert Settings.from_dict({"cloud_upload_destination":"azure_blob"}).cloud_upload_destination == "azure_blob"
    assert Settings.from_dict({"cloud_upload_destination":"invalid"}).cloud_upload_destination == "http"
