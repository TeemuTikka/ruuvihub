"""Verify Windows installer publication and staging use the signed artifact."""

import io
from pathlib import Path
import sys

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_release import build_archive, build_metadata
import release_fetch
from release_metadata import METADATA_NAME, SIGNATURE_NAME, sign_release, windows_installer_name
from windows_release import stage_windows_installer, verify_installer

VERSION = "v1.2.3"


@pytest.fixture
def release(tmp_path, monkeypatch):
    archive = build_archive(VERSION, output_dir=tmp_path)
    installer = tmp_path / windows_installer_name(VERSION)
    installer.write_bytes(b"fixture installer")
    key = Ed25519PrivateKey.generate()
    metadata = build_metadata(VERSION, archive, "v1.0.0", windows_installer=installer)
    (tmp_path / METADATA_NAME).write_bytes(metadata)
    signature = key.sign(metadata)
    responses = {METADATA_NAME: metadata, SIGNATURE_NAME: signature, installer.name: installer.read_bytes()}
    monkeypatch.setattr(
        release_fetch, "_open",
        lambda url, *, asset: io.BytesIO(
            responses[url.rsplit("/", 1)[-1]] if asset else
            b'{"draft":false,"prerelease":false,"tag_name":"v1.2.3"}'
        ),
    )
    return installer, key, responses


def test_stages_and_verifies_the_signed_installer(release, tmp_path):
    installer, key, _ = release
    staging = tmp_path / "staging"
    staging.mkdir()
    artifact, executable = stage_windows_installer(key.public_key(), staging)
    assert artifact.version == VERSION
    assert executable.read_bytes() == installer.read_bytes()
    verify_installer(key.public_key(), VERSION, executable)
    with pytest.raises(FileExistsError):
        stage_windows_installer(key.public_key(), staging)
    assert executable.read_bytes() == installer.read_bytes()


@pytest.mark.parametrize("asset", ["signature", "installer"])
def test_rejects_modified_download_without_leaving_an_installer(release, tmp_path, asset):
    installer, key, responses = release
    name = SIGNATURE_NAME if asset == "signature" else installer.name
    responses[name] = bytes(len(responses[name]))
    staging = tmp_path / "staging"
    staging.mkdir()
    with pytest.raises(ValueError, match="signature|SHA-256"):
        stage_windows_installer(key.public_key(), staging)
    assert not list(staging.iterdir())


def test_publisher_does_not_sign_when_windows_installer_changed(release, tmp_path):
    installer, key, _ = release
    args = (tmp_path, VERSION, "v1.0.0", key.private_bytes_raw().hex(), key.public_key().public_bytes_raw().hex())
    contents = installer.read_bytes()
    installer.write_bytes(bytes(len(contents)))
    with pytest.raises(ValueError, match="SHA-256"):
        sign_release(*args)
    assert not (tmp_path / SIGNATURE_NAME).exists()
    installer.write_bytes(contents)
    sign_release(*args)
    assert len((tmp_path / SIGNATURE_NAME).read_bytes()) == 64
