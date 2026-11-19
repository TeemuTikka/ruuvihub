"""Verify or stage a signed Windows installer without running it."""

from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from release_fetch import download_artifact, fetch_artifact, latest_version
from release_metadata import Artifact, WINDOWS_PLATFORM, windows_installer_name


def windows_installer(public_key: Ed25519PublicKey, version: str | None = None) -> Artifact:
    artifact = fetch_artifact(public_key, version if version is not None else latest_version(), WINDOWS_PLATFORM)
    if artifact.name != windows_installer_name(artifact.version):
        raise ValueError("unexpected Windows release installer name")
    return artifact


def verify_installer(public_key: Ed25519PublicKey, version: str, path: Path) -> None:
    windows_installer(public_key, version).verify_file(path)


def stage_windows_installer(public_key: Ed25519PublicKey, staging: Path) -> tuple[Artifact, Path]:
    """Download only to a caller-owned staging directory; never replace an existing file."""
    artifact = windows_installer(public_key)
    path = staging / artifact.name
    download_artifact(artifact, path)
    return artifact, path
