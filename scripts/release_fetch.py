"""Fetch signed release metadata and artifacts from the approved GitHub repository."""

import json
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from release_metadata import Artifact, METADATA_NAME, RELEASE_DOWNLOAD, SIGNATURE_NAME, STABLE_VERSION, verify_metadata

UPDATER_VERSION = "v1.0.0"
_LATEST_URL = "https://api.github.com/repos/TeemuTikka/ruuvihub/releases/latest"
_ASSET_HOST = "release-assets.githubusercontent.com"
_CHUNK_SIZE = 1024 * 1024


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
    headers = {"User-Agent": "RuuviHub release client"}
    if not asset:
        headers["Accept"] = "application/vnd.github+json"
    return opener.open(Request(url, headers=headers), timeout=15)


def _read(url: str, limit: int, *, asset: bool = False) -> bytes:
    with _open(url, asset=asset) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("release response exceeds the expected size")
    return data


def _stable_tag(version: object) -> bool:
    return isinstance(version, str) and len(version) <= 64 and STABLE_VERSION.fullmatch(version) is not None


def latest_version() -> str:
    release = json.loads(_read(_LATEST_URL, _CHUNK_SIZE))
    if not isinstance(release, dict) or release.get("draft") is not False or release.get("prerelease") is not False:
        raise ValueError("GitHub did not return a stable release")
    version = release.get("tag_name")
    if not _stable_tag(version):
        raise ValueError("GitHub did not return a stable RuuviHub tag")
    return version


def fetch_artifact(public_key: Ed25519PublicKey, version: str, platform: str) -> Artifact:
    if not _stable_tag(version):
        raise ValueError("expected a stable RuuviHub tag")
    base_url = f"{RELEASE_DOWNLOAD}/{version}"
    return verify_metadata(
        _read(f"{base_url}/{METADATA_NAME}", 16 * 1024, asset=True),
        _read(f"{base_url}/{SIGNATURE_NAME}", 64, asset=True),
        public_key,
        expected_version=version,
        updater_version=UPDATER_VERSION,
        platform=platform,
    )


def download_artifact(artifact: Artifact, destination: Path) -> None:
    output = destination.open("xb")
    try:
        with output, _open(artifact.url, asset=True) as response:
            remaining = artifact.size
            while remaining and (chunk := response.read(min(remaining, _CHUNK_SIZE))):
                output.write(chunk)
                remaining -= len(chunk)
            if remaining or response.read(1):
                raise ValueError("download size does not match signed metadata")
        artifact.verify_file(destination)
    except BaseException:
        destination.unlink()
        raise
