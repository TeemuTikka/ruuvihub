"""Tests for src/config.py."""

import json
import os
import tempfile

import pytest

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from config import Config, load_config


def write_config(tmp_path, data: dict) -> str:
    path = os.path.join(tmp_path, "config.json")
    with open(path, "w") as f:
        json.dump(data, f)
    return path


def test_load_defaults(tmp_path):
    path = write_config(tmp_path, {})
    config = load_config(path)
    assert config.scan_enabled is True
    assert config.scan_interval_seconds == 60
    assert config.scan_timeout_seconds == 5
    assert config.upload_interval_seconds == 300
    assert config.max_entries_per_tag == 100
    assert config.tag_addresses == []
    assert config.db_retention_days == 0
    assert config.log_path == ""
    assert config.datetime_format == "fi"


def test_load_custom_values(tmp_path):
    data = {
        "datetime_format": "iso",
        "scan_enabled": False,
        "scan_interval_seconds": 30,
        "scan_timeout_seconds": 10,
        "upload_interval_seconds": 120,
        "max_entries_per_tag": 50,
        "tag_addresses": ["AA:BB:CC:DD:EE:01"],
        "db_retention_days": 14,
        "log_path": "/var/log/ruuvihub.log",
    }
    path = write_config(tmp_path, data)
    config = load_config(path)
    assert config.scan_enabled is False
    assert config.scan_interval_seconds == 30
    assert config.scan_timeout_seconds == 10
    assert config.upload_interval_seconds == 120
    assert config.max_entries_per_tag == 50
    assert config.tag_addresses == ["AA:BB:CC:DD:EE:01"]
    assert config.db_retention_days == 14
    assert config.log_path == "/var/log/ruuvihub.log"
    assert config.datetime_format == "iso"


@pytest.mark.parametrize("value", [None, False, 123, "", "en-US", {}, []])
def test_invalid_datetime_format_falls_back_to_finnish(tmp_path, value):
    assert load_config(write_config(tmp_path, {"datetime_format": value})).datetime_format == "fi"


def test_load_via_env_var(tmp_path, monkeypatch):
    path = write_config(tmp_path, {"scan_interval_seconds": 15})
    monkeypatch.setenv("CONFIG_PATH", path)
    config = load_config()
    assert config.scan_interval_seconds == 15


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/config.json")


def test_config_is_dataclass():
    config = Config()
    assert hasattr(config, "scan_interval_seconds")
    assert hasattr(config, "tag_addresses")


def test_https_defaults(tmp_path):
    path = write_config(tmp_path, {})
    config = load_config(path)
    assert config.https_enabled is True
    assert config.https_port == 8443
    assert config.tls_cert_path == ""
    assert config.tls_key_path == ""
    assert config.https_redirect_http is True


def test_https_custom_values(tmp_path):
    data = {
        "https_enabled": False,
        "https_port": 9443,
        "tls_cert_path": "/etc/ruuvihub/cert.pem",
        "tls_key_path": "/etc/ruuvihub/key.pem",
        "https_redirect_http": False,
    }
    path = write_config(tmp_path, data)
    config = load_config(path)
    assert config.https_enabled is False
    assert config.https_port == 9443
    assert config.tls_cert_path == "/etc/ruuvihub/cert.pem"
    assert config.tls_key_path == "/etc/ruuvihub/key.pem"
    assert config.https_redirect_http is False


def test_https_port_rejects_invalid_value(tmp_path):
    path = write_config(tmp_path, {"https_port": -1})
    config = load_config(path)
    assert config.https_port == 8443  # falls back to default


def test_https_port_rejects_out_of_range_value(tmp_path):
    path = write_config(tmp_path, {"https_port": 65536})
    config = load_config(path)
    assert config.https_port == 8443  # falls back to default (not a valid TCP port)
