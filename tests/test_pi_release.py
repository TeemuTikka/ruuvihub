"""Verify authenticated Pi releases can be staged without touching live state."""

import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
from urllib.request import Request

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import pi_release
from release_metadata import Artifact

VERSION = "v1.1.0"
NAME = f"ruuvihub-{VERSION}.tar.gz"
RELEASE = {"tag_name": VERSION, "draft": False, "prerelease": False}
FILES = {
    "VERSION": b"v1.1.0\n",
    "config.json": b"{}",
    "requirements.txt": b"# wheel lock",
    "src/main.py": b"# application",
}


def _archive(extra=(), files=FILES):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for path, data in files.items():
            member = tarfile.TarInfo(f"ruuvihub-{VERSION}/{path}")
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
        for member in extra:
            archive.addfile(member, io.BytesIO())
    return output.getvalue()


def _asset(content):
    return Artifact(VERSION, NAME, len(content), hashlib.sha256(content).hexdigest())


@pytest.fixture
def release_assets(monkeypatch):
    archive = _archive()
    artifact = _asset(archive)
    key = Ed25519PrivateKey.generate()
    metadata = json.dumps({
        "version": VERSION,
        "minimum_updater_version": "v1.0.0",
        "artifacts": {"linux-arm64": {"name": NAME, "size": artifact.size, "sha256": artifact.sha256}},
    }).encode()
    assets = {
        "https://api.github.com/repos/TeemuTikka/ruuvihub/releases/latest": json.dumps(RELEASE).encode(),
        artifact.url: archive,
        artifact.url.rsplit("/", 1)[0] + "/release-metadata.json": metadata,
        artifact.url.rsplit("/", 1)[0] + "/release-metadata.json.sig": key.sign(metadata),
    }

    def open_asset(url, *, asset=False):
        assert asset == url.startswith("https://github.com/")
        return io.BytesIO(assets[url])

    monkeypatch.setattr(pi_release, "_open", open_asset)
    return assets, artifact, key.public_key()


def test_stages_signed_release_without_touching_live_files(tmp_path, release_assets):
    _, artifact, public_key = release_assets
    active = tmp_path / "live-app"
    active.write_bytes(b"already installed")
    assert pi_release.latest_pi_artifact(public_key) == artifact
    staged = pi_release.stage_pi_release(artifact, tmp_path)
    assert {path: (staged / path).read_bytes() for path in FILES} == FILES
    assert active.read_bytes() == b"already installed"


@pytest.mark.parametrize("changes", [{"draft": True}, {"prerelease": True}, {"tag_name": "../v1.1.0"}])
def test_rejects_unexpected_release_before_fetching_assets(release_assets, changes):
    assets, _, public_key = release_assets
    api = next(iter(assets))
    assets.clear()
    assets[api] = json.dumps(RELEASE | changes).encode()
    with pytest.raises(ValueError, match="stable"):
        pi_release.latest_pi_artifact(public_key)


def test_rejects_bad_signature_before_requesting_archive(release_assets):
    assets, artifact, public_key = release_assets
    assets.pop(artifact.url)
    signature = next(url for url in assets if url.endswith(".sig"))
    assets[signature] = bytes(64)
    with pytest.raises(ValueError, match="signature"):
        pi_release.latest_pi_artifact(public_key)


@pytest.mark.parametrize("change", ["truncate", "append", "mutate"])
def test_rejects_changed_archive_before_staging(tmp_path, release_assets, change):
    assets, artifact, _ = release_assets
    original = assets[artifact.url]
    assets[artifact.url] = {
        "truncate": original[1:],
        "append": original + b"x",
        "mutate": bytes([original[0] ^ 1]) + original[1:],
    }[change]
    with pytest.raises(ValueError, match="signed metadata"):
        pi_release.stage_pi_release(artifact, tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("path, kind", [
    ("../escape", tarfile.REGTYPE),
    ("src\\escape.py", tarfile.REGTYPE),
    ("VERSION", tarfile.REGTYPE),
    ("venv", tarfile.REGTYPE),
    ("src/linked.py", tarfile.SYMTYPE),
    ("src/hardlink.py", tarfile.LNKTYPE),
    ("src/fifo", tarfile.FIFOTYPE),
])
def test_rejects_unsafe_entries_and_cleans_up(tmp_path, monkeypatch, path, kind):
    member = tarfile.TarInfo(f"ruuvihub-{VERSION}/{path}")
    member.type = kind
    malicious = _archive([member])
    monkeypatch.setattr(pi_release, "_open", lambda *args, **kwargs: io.BytesIO(malicious))
    with pytest.raises(ValueError, match="unsafe entry"):
        pi_release.stage_pi_release(_asset(malicious), tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("files", [
    {key: data for key, data in FILES.items() if key != "requirements.txt"},
    FILES | {"VERSION": b"v1.1.1\n"},
])
def test_staging_requires_complete_matching_release(tmp_path, monkeypatch, files):
    content = _archive(files=files)
    monkeypatch.setattr(pi_release, "_open", lambda *args, **kwargs: io.BytesIO(content))
    with pytest.raises(ValueError, match="contents do not match"):
        pi_release.stage_pi_release(_asset(content), tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("allowed, target", [
    (True, "http://release-assets.githubusercontent.com/download"),
    (True, "https://release-assets.githubusercontent.com.example/download"),
    (True, "https://example.com/download"),
    (False, "https://release-assets.githubusercontent.com/download"),
])
def test_rejects_unapproved_redirects(allowed, target):
    request = Request(_asset(b"archive").url)
    policy = pi_release._ReleaseRedirects(allowed)
    with pytest.raises(ValueError, match="unexpected GitHub release redirect"):
        policy.redirect_request(request, None, 302, "", {}, target)


def test_allows_github_asset_delivery_but_no_further_redirects():
    policy = pi_release._ReleaseRedirects(True)
    request = Request(_asset(b"archive").url)
    target = "https://release-assets.githubusercontent.com/download"
    redirected = policy.redirect_request(request, None, 302, "", {}, target)
    assert redirected.full_url == target
    with pytest.raises(ValueError, match="unexpected GitHub release redirect"):
        policy.redirect_request(redirected, None, 302, "", {}, target)
