"""Validate release version selection and the artifact's public contents."""

from pathlib import Path
import sys
import tarfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_release import build_archive
from next_release_tag import next_release_tag


@pytest.mark.parametrize("tags, expected", [
    ([], "v1.1.0"),
    (["v1.0.9", "v1.0.11"], "v1.1.0"),
    (["v1.1.0"], "v1.1.1"),
    (["v1.1.9", "v1.1.10"], "v1.1.11"),
    (["v2.0.0", "v1.99.99"], "v2.0.1"),
    (["v9.0.0-beta", "v1.01.0", "v1.1.0-extra", "unrelated"], "v1.1.0"),
])
def test_next_release_tag(tags, expected):
    assert next_release_tag(tags) == expected


def test_invalid_release_floor_is_rejected():
    with pytest.raises(ValueError):
        next_release_tag([], "../v1.1.0")


@pytest.fixture
def release_source(tmp_path):
    root = tmp_path / "source"
    files = [
        "src/main.py", "scripts/install.sh", "scripts/update.sh", "scripts/rollback.sh",
        "scripts/uninstall.sh", "scripts/lib.sh", "scripts/ruuvihub.service.template",
        "tests/test_connection.py", "config.json", "requirements.txt", "README-pi.md",
        "THIRD_PARTY_NOTICES.md", "third_party_licenses/Chart.js-MIT.txt",
        "third_party_licenses/kurkle-color-MIT.txt",
        "LICENSE", "SECURITY.md",
    ]
    for name in files:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic release fixture\n", encoding="utf-8")
    return root


def test_release_archive_contents(release_source, tmp_path):
    # Runtime files outside the packaged directories must never be copied.
    (release_source / "settings.local.json").write_text("private fixture")
    (release_source / "readings.db").write_bytes(b"private fixture")
    cache = release_source / "src/__pycache__"
    cache.mkdir()
    (cache / "main.pyc").write_bytes(b"bytecode fixture")
    archive_path = build_archive("v1.1.0", release_source, tmp_path / "output")
    prefix = "ruuvihub-v1.1.0/"
    with tarfile.open(archive_path) as archive:
        names = {name.removeprefix(prefix) for name in archive.getnames()}
        assert {"VERSION", "src/main.py", "scripts/test_connection.py", "LICENSE", "SECURITY.md",
                "THIRD_PARTY_NOTICES.md", "third_party_licenses/Chart.js-MIT.txt"} <= names
        assert all(name.startswith(prefix) for name in archive.getnames())
        assert not any("settings.local" in name or "__pycache__" in name
                       or name.endswith(".db") or name.startswith("tests/") for name in names)
        assert archive.extractfile(prefix + "VERSION").read() == b"v1.1.0\n"


@pytest.mark.parametrize("name", ["src/settings.local.json", "scripts/backup.db", "src/certs/key.pem"])
def test_nested_private_files_are_rejected(release_source, tmp_path, name):
    path = release_source / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("private fixture")
    with pytest.raises(ValueError, match="Forbidden release input"):
        build_archive("v1.1.0", release_source, tmp_path / "output")
    assert not (tmp_path / "output/ruuvihub-v1.1.0.tar.gz").exists()


@pytest.mark.parametrize("version", ["../outside", "v1.1.0\nBAD=value", "v1.1.0-rc1", "v01.1.0"])
def test_archive_version_cannot_escape_output_directory(release_source, tmp_path, version):
    with pytest.raises(ValueError, match="stable vX.Y.Z"):
        build_archive(version, release_source, tmp_path / "output")


@pytest.mark.parametrize("name", ["scripts/install.sh", "LICENSE", "SECURITY.md"])
def test_missing_required_release_file_is_rejected(release_source, tmp_path, name):
    (release_source / name).unlink()
    with pytest.raises(FileNotFoundError, match="Missing required release input"):
        build_archive("v1.1.0", release_source, tmp_path / "output")
