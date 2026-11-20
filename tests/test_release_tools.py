"""Validate release version selection and the artifact's public contents."""

import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_release import build_archive, build_metadata
from next_release_tag import next_release_tag
from release_metadata import METADATA_NAME, SIGNATURE_NAME, sign_release, verify_metadata


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


def test_release_metadata_describes_archive(release_source, tmp_path):
    archive_path = build_archive("v1.1.0", release_source, tmp_path / "output")
    metadata = json.loads(build_metadata("v1.1.0", archive_path, "v1.0.0"))
    assert metadata == {
        "version": "v1.1.0",
        "minimum_updater_version": "v1.0.0",
        "artifacts": {
            "linux-arm64": {
                "name": archive_path.name,
                "size": archive_path.stat().st_size,
                "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
            }
        },
    }


@pytest.mark.parametrize("version, minimum", [
    ("v1.1.0-rc1", "v1.0.0"), ("v1.1.0", "../v1.0.0"),
])
def test_metadata_rejects_invalid_versions(release_source, tmp_path, version, minimum):
    archive_path = build_archive("v1.1.0", release_source, tmp_path / "output")
    with pytest.raises(ValueError, match="stable vX.Y.Z"):
        build_metadata(version, archive_path, minimum)


def test_metadata_rejects_archive_name_mismatch(release_source, tmp_path):
    archive_path = build_archive("v1.1.0", release_source, tmp_path / "output")
    with pytest.raises(ValueError, match="archive name"):
        build_metadata("v1.1.1", archive_path, "v1.0.0")


@pytest.mark.parametrize("versions", [[], [b"v1.1.1\n"], [b"v1.1.0\n", b"v1.1.0\n"]])
def test_metadata_rejects_invalid_archive_version(tmp_path, versions):
    archive_path = tmp_path / "ruuvihub-v1.1.0.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        for data in versions:
            member = tarfile.TarInfo("ruuvihub-v1.1.0/VERSION")
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    with pytest.raises(ValueError, match="VERSION"):
        build_metadata("v1.1.0", archive_path, "v1.0.0")


@pytest.fixture
def release_metadata(release_source, tmp_path):
    directory = tmp_path / "output"
    archive_path = build_archive("v1.1.0", release_source, directory)
    metadata = build_metadata("v1.1.0", archive_path, "v1.2.0")
    (directory / METADATA_NAME).write_bytes(metadata)
    return archive_path, metadata, Ed25519PrivateKey.generate()


def _verify(metadata, signature, key, *, version="v1.1.0", updater="v1.10.0", platform="linux-arm64"):
    return verify_metadata(
        metadata, signature, key.public_key(),
        expected_version=version, updater_version=updater, platform=platform,
    )


def test_sign_and_verify_release(release_metadata):
    archive_path, metadata, key = release_metadata
    sign_release(
        archive_path.parent, "v1.1.0", "v1.10.0",
        key.private_bytes_raw().hex(), key.public_key().public_bytes_raw().hex(),
    )
    signature = (archive_path.parent / SIGNATURE_NAME).read_bytes()
    artifact = _verify(metadata, signature, key)
    artifact.verify_file(archive_path)
    assert artifact.url == (
        "https://github.com/TeemuTikka/ruuvihub/releases/download/v1.1.0/ruuvihub-v1.1.0.tar.gz"
    )


def test_signing_rejects_mismatched_trust_key(release_metadata):
    archive_path, _, key = release_metadata
    other_key = Ed25519PrivateKey.generate()
    with pytest.raises(ValueError, match="invalid release metadata signature"):
        sign_release(
            archive_path.parent, "v1.1.0", "v1.10.0",
            key.private_bytes_raw().hex(), other_key.public_key().public_bytes_raw().hex(),
        )
    assert not (archive_path.parent / SIGNATURE_NAME).exists()


@pytest.mark.parametrize("changes, error", [
    ({"version": "v1.1.1"}, "selected release"),
    ({"updater": "v1.1.9"}, "newer updater"),
    ({"platform": "windows-x64"}, "artifact for this platform"),
])
def test_verification_rejects_incompatible_release(release_metadata, changes, error):
    _, metadata, key = release_metadata
    with pytest.raises(ValueError, match=error):
        _verify(metadata, key.sign(metadata), key, **changes)


def test_verification_rejects_tampered_metadata(release_metadata):
    _, metadata, key = release_metadata
    with pytest.raises(ValueError, match="invalid release metadata signature"):
        _verify(metadata + b"\n", key.sign(metadata), key)


@pytest.mark.parametrize("name", ["../archive.tar.gz", "https://example.com/archive.tar.gz", "a%2fb"])
def test_verification_rejects_unsafe_asset_names(release_metadata, name):
    _, metadata, key = release_metadata
    document = json.loads(metadata)
    document["artifacts"]["linux-arm64"]["name"] = name
    payload = json.dumps(document).encode()
    with pytest.raises(ValueError, match="invalid release artifact metadata"):
        _verify(payload, key.sign(payload), key)


@pytest.mark.parametrize("truncate, error", [(True, "size"), (False, "SHA-256")])
def test_verification_rejects_changed_archive(release_metadata, truncate, error):
    archive_path, metadata, key = release_metadata
    artifact = _verify(metadata, key.sign(metadata), key)
    data = archive_path.read_bytes()
    archive_path.write_bytes(data[1:] if truncate else bytes([data[0] ^ 1]) + data[1:])
    with pytest.raises(ValueError, match=error):
        artifact.verify_file(archive_path)


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
