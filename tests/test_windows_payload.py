"""Verify the runtime archive is checked before packaging."""

import hashlib
from pathlib import Path
import sys
from zipfile import ZipFile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_windows_payload import _extract_runtime
from windows_launch import prepare_data


def test_runtime_archive_hash_checked_before_extraction(tmp_path):
    archive = tmp_path / "python.zip"
    with ZipFile(archive, "w") as bundle:
        bundle.writestr("python.exe", b"fixture")
        bundle.writestr("LICENSE.txt", b"fixture")
    destination = tmp_path / "runtime"
    destination.mkdir()
    with pytest.raises(ValueError, match="SHA-256 does not match"):
        _extract_runtime(archive, "0" * 64, destination)
    assert not list(destination.iterdir())
    _extract_runtime(archive, hashlib.sha256(archive.read_bytes()).hexdigest(), destination)
    assert (destination / "python.exe").read_bytes() == b"fixture"


@pytest.mark.parametrize("name", ["../python.exe", "python.exe:stream", "LICENSE.TXT", "some\\python.exe"])
def test_runtime_archive_rejects_non_flat_or_ambiguous_names(tmp_path, name):
    archive = tmp_path / "python.zip"
    with ZipFile(archive, "w") as bundle:
        bundle.writestr("LICENSE.txt", b"fixture")
        bundle.writestr(name, b"fixture")
    destination = tmp_path / "runtime"
    destination.mkdir()
    with pytest.raises(ValueError, match="unexpected filename"):
        _extract_runtime(archive, hashlib.sha256(archive.read_bytes()).hexdigest(), destination)


def test_version_switch_keeps_persistent_settings_and_config(tmp_path):
    import json

    data = tmp_path / "data"
    data.mkdir()
    settings = data / "settings.local.json"
    settings.write_text("existing settings")
    for version, interval in [("v1.2.0", 11), ("v1.2.1", 23)]:
        release = tmp_path / "releases" / version
        release.mkdir(parents=True)
        (release / "config.json").write_text(json.dumps({"scan_interval_seconds": interval}))
        prepare_data(release, data)
        persisted = json.loads((data / "config.json").read_bytes())
        assert persisted == {
            "scan_interval_seconds": 11,
            "db_path": str(data / "ruuvi_data.db"),
            "log_path": str(data / "logs" / "ruuvihub.log"),
            "tls_cert_path": str(data / "certs" / "ruuvihub.crt"),
            "tls_key_path": str(data / "certs" / "ruuvihub.key"),
        }
        assert settings.read_text() == "existing settings"
        assert not list(data.glob(".config-*"))
        with pytest.raises(ValueError, match="must be separate"):
            prepare_data(release, release / "data")
