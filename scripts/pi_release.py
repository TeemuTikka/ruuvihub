"""Download and stage an authenticated Pi release without activating it."""

import json
from pathlib import Path
import re
import shutil
import tarfile
from tempfile import mkdtemp, TemporaryDirectory
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from release_metadata import (
    Artifact,
    METADATA_NAME,
    RELEASE_DOWNLOAD,
    SIGNATURE_NAME,
    STABLE_VERSION,
    verify_metadata,
)

UPDATER_VERSION = "v1.0.0"
_LATEST_URL = "https://api.github.com/repos/TeemuTikka/ruuvihub/releases/latest"
_ASSET_HOST = "release-assets.githubusercontent.com"
_CHUNK_SIZE = 1024 * 1024
_MAX_EXTRACTED_BYTES = 64 * _CHUNK_SIZE
_COMPONENT = re.compile(r"[a-zA-Z0-9_][a-zA-Z0-9._-]*")
_REQUIRED = {"VERSION", "config.json", "requirements.txt", "src/main.py"}


class _ReleaseRedirects(HTTPRedirectHandler):
    def __init__(self, allow_asset_redirect: bool):
        self.allow_asset_redirect = allow_asset_redirect

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        source, target = urlsplit(req.full_url), urlsplit(newurl)
        if not (
            self.allow_asset_redirect
            and source.scheme == "https"
            and source.netloc == "github.com"
            and target.scheme == "https"
            and target.netloc == _ASSET_HOST
        ):
            raise ValueError("unexpected GitHub release redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url: str, *, asset: bool = False):
    opener = build_opener(_ReleaseRedirects(allow_asset_redirect=asset))
    headers = {"User-Agent": "RuuviHub Pi updater"}
    if not asset:
        headers["Accept"] = "application/vnd.github+json"
    return opener.open(Request(url, headers=headers), timeout=15)


def _read(url: str, limit: int, *, asset: bool = False) -> bytes:
    with _open(url, asset=asset) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("release response exceeds the expected size")
    return data


def _validate_artifact_name(artifact: Artifact) -> None:
    if not STABLE_VERSION.fullmatch(artifact.version) or artifact.name != f"ruuvihub-{artifact.version}.tar.gz":
        raise ValueError("unexpected Pi release archive name")


def latest_pi_artifact(public_key: Ed25519PublicKey) -> Artifact:
    release = json.loads(_read(_LATEST_URL, _CHUNK_SIZE))
    if not isinstance(release, dict) or release.get("draft") is not False or release.get("prerelease") is not False:
        raise ValueError("GitHub did not return a stable release")
    version = release.get("tag_name")
    if not isinstance(version, str) or len(version) > 64 or not STABLE_VERSION.fullmatch(version):
        raise ValueError("GitHub did not return a stable RuuviHub tag")
    base_url = f"{RELEASE_DOWNLOAD}/{version}"
    artifact = verify_metadata(
        _read(f"{base_url}/{METADATA_NAME}", 16 * 1024, asset=True),
        _read(f"{base_url}/{SIGNATURE_NAME}", 64, asset=True),
        public_key,
        expected_version=version,
        updater_version=UPDATER_VERSION,
        platform="linux-arm64",
    )
    _validate_artifact_name(artifact)
    return artifact


def _extract(archive_path: Path, destination: Path, version: str) -> None:
    names = set()
    total_size = 0
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive:
            root, _, name = member.name.partition("/")
            parts = name.split("/")
            if (
                root != f"ruuvihub-{version}"
                or not all(_COMPONENT.fullmatch(part) and not part.endswith(".") for part in parts)
                or any(part.lower() == "venv" for part in parts)
                or member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE)
                or name in names
            ):
                raise ValueError("unsafe entry in Pi release archive")
            names.add(name)
            total_size += member.size
            if member.size < 0 or total_size > _MAX_EXTRACTED_BYTES or len(names) > 4096:
                raise ValueError("Pi release archive is too large")
            target = destination.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output, length=_CHUNK_SIZE)
            target.chmod(0o644 | (member.mode & 0o111))
    if not _REQUIRED <= names or (destination / "VERSION").read_bytes() != f"{version}\n".encode("ascii"):
        raise ValueError("Pi release contents do not match the selected release")


def stage_pi_release(artifact: Artifact, staging_parent: Path) -> Path:
    """Stage an artifact returned by latest_pi_artifact under a trusted parent."""
    _validate_artifact_name(artifact)
    with TemporaryDirectory(prefix="ruuvihub-download-") as directory:
        archive_path = Path(directory) / "release.tar.gz"
        remaining = artifact.size
        with _open(artifact.url, asset=True) as response, archive_path.open("xb") as output:
            while remaining and (chunk := response.read(min(remaining, _CHUNK_SIZE))):
                output.write(chunk)
                remaining -= len(chunk)
            if remaining or response.read(1):
                raise ValueError("download size does not match signed metadata")
        artifact.verify_file(archive_path)

        destination = Path(mkdtemp(prefix=".ruuvihub-stage-", dir=staging_parent))
        try:
            _extract(archive_path, destination, artifact.version)
        except BaseException:
            shutil.rmtree(destination)
            raise
        return destination
