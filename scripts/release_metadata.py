#!/usr/bin/env python3
"""Sign and verify release metadata and check the selected artifact."""

import argparse
from dataclasses import dataclass
from hashlib import file_digest
import json
import os
from pathlib import Path
import re

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

METADATA_NAME = "release-metadata.json"
SIGNATURE_NAME = "release-metadata.json.sig"
RELEASE_DOWNLOAD = "https://github.com/TeemuTikka/ruuvihub/releases/download"
STABLE_VERSION = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
_MAX_METADATA_BYTES = 16 * 1024
WINDOWS_PLATFORM = "windows-x64"


def windows_installer_name(version: str) -> str:
    return f"ruuvihub-{version}-windows-x64.exe"


def _version_parts(value: str) -> tuple[int, ...]:
    match = STABLE_VERSION.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise ValueError("versions must be stable vX.Y.Z tags")
    return tuple(map(int, match.groups()))


@dataclass(frozen=True)
class Artifact:
    version: str
    name: str
    size: int
    sha256: str

    @property
    def url(self) -> str:
        return f"{RELEASE_DOWNLOAD}/{self.version}/{self.name}"

    def verify_file(self, path: Path) -> None:
        with path.open("rb") as artifact:
            if os.fstat(artifact.fileno()).st_size != self.size:
                raise ValueError("artifact size does not match signed metadata")
            if file_digest(artifact, "sha256").hexdigest() != self.sha256:
                raise ValueError("artifact SHA-256 does not match signed metadata")


def verify_metadata(
    metadata: bytes,
    signature: bytes,
    public_key: Ed25519PublicKey,
    *,
    expected_version: str,
    updater_version: str,
    platform: str,
) -> Artifact:
    if len(metadata) > _MAX_METADATA_BYTES or len(signature) != 64:
        raise ValueError("invalid release metadata or signature size")
    try:
        public_key.verify(signature, metadata)
    except InvalidSignature:
        raise ValueError("invalid release metadata signature") from None

    document = json.loads(metadata)
    if not isinstance(document, dict) or document.get("version") != expected_version:
        raise ValueError("metadata version does not match the selected release")
    _version_parts(expected_version)
    if _version_parts(updater_version) < _version_parts(document.get("minimum_updater_version")):
        raise ValueError("release requires a newer updater")

    artifacts = document.get("artifacts")
    artifact = artifacts.get(platform) if isinstance(artifacts, dict) else None
    if not isinstance(artifact, dict):
        raise ValueError("release does not include an artifact for this platform")
    name, size, sha256 = artifact.get("name"), artifact.get("size"), artifact.get("sha256")
    if (
        not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}", name)
        or type(size) is not int or size <= 0
        or not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256)
    ):
        raise ValueError("invalid release artifact metadata")
    return Artifact(expected_version, name, size, sha256)


def sign_release(
    directory: Path, version: str, updater_version: str, private_key_hex: str, public_key_hex: str
) -> None:
    """Write a detached signature only after checking the matching release artifact."""
    try:
        private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
    except ValueError:
        raise ValueError("release signing keys must each be a 32-byte hex value") from None

    metadata = (directory / METADATA_NAME).read_bytes()
    signature = private_key.sign(metadata)
    artifact = verify_metadata(
        metadata, signature, public_key,
        expected_version=version, updater_version=updater_version, platform="linux-arm64",
    )
    if artifact.name != f"ruuvihub-{version}.tar.gz":
        raise ValueError("unexpected Pi release archive name")
    artifact.verify_file(directory / artifact.name)
    if WINDOWS_PLATFORM in json.loads(metadata)["artifacts"]:
        installer = verify_metadata(
            metadata, signature, public_key, expected_version=version,
            updater_version=updater_version, platform=WINDOWS_PLATFORM,
        )
        if installer.name != windows_installer_name(version):
            raise ValueError("unexpected Windows release installer name")
        installer.verify_file(directory / installer.name)
    (directory / SIGNATURE_NAME).write_bytes(signature)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sign built RuuviHub release metadata.")
    parser.add_argument("directory", type=Path)
    parser.add_argument("version")
    parser.add_argument("updater_version")
    args = parser.parse_args()
    try:
        sign_release(
            args.directory, args.version, args.updater_version,
            os.environ.get("RELEASE_SIGNING_PRIVATE_KEY", ""),
            os.environ.get("RELEASE_SIGNING_PUBLIC_KEY", ""),
        )
    except (OSError, ValueError) as error:
        parser.exit(1, f"Release signing failed: {error}\n")
