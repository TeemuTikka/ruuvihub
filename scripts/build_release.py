#!/usr/bin/env python3
"""Build the same release archive locally and in CI, without local runtime state."""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
_REQUIRED = (
    "src", "scripts", "config.json", "requirements.txt", "README-pi.md",
    "THIRD_PARTY_NOTICES.md", "third_party_licenses", "LICENSE", "SECURITY.md",
)
_PRIVATE_DIRECTORIES = {".git", ".venv", "certs", "logs"}
_VERSION = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
_REQUIRED_FILES = (
    "src/main.py", "scripts/install.sh", "scripts/update.sh", "scripts/rollback.sh",
    "scripts/uninstall.sh", "scripts/lib.sh", "scripts/ruuvihub.service.template",
    "third_party_licenses/Chart.js-MIT.txt", "third_party_licenses/kurkle-color-MIT.txt",
)


def collect_release_files(root: Path, sources: list[tuple[Path, str]]) -> list[tuple[Path, Path]]:
    files = []
    for source, destination in sources:
        if not source.exists():
            raise FileNotFoundError(f"Missing required release input: {source.relative_to(root)}")
        candidates = [source, *sorted(source.rglob("*"))] if source.is_dir() else [source]
        for path in candidates:
            relative = path.relative_to(root)
            if "__pycache__" in relative.parts or path.suffix in {".pyc", ".pyo"}:
                continue
            if (path.is_symlink() or _PRIVATE_DIRECTORIES.intersection(relative.parts)
                    or path.name.startswith(("settings.local.json", ".env"))
                    or re.search(r"\.(?:db|sqlite3?|pem|key|crt|pfx|p12|log)(?:$|[.-])", path.name, re.I)):
                raise ValueError(f"Forbidden release input: {relative}")
            if path.is_file():
                target = Path(destination) / path.relative_to(source) if source.is_dir() else Path(destination)
                files.append((path, target))
    return files


def build_archive(version: str, root: Path = ROOT, output_dir: Path = Path(".")) -> Path:
    if _VERSION.fullmatch(version) is None:
        raise ValueError("version must be a stable vX.Y.Z tag")
    for name in _REQUIRED_FILES:
        if not (root / name).is_file():
            raise FileNotFoundError(f"Missing required release input: {name}")
    sources = [(root / name, name) for name in _REQUIRED]
    sources.append((root / "tests/test_connection.py", "scripts/test_connection.py"))
    files = collect_release_files(root, sources)
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = output_dir / f"ruuvihub-{version}.tar.gz"
    fd, temporary_path = tempfile.mkstemp(dir=output_dir, suffix=".tar.gz")
    os.close(fd)
    try:
        with tarfile.open(temporary_path, "w:gz") as archive:
            for path, target in files:
                archive.add(path, arcname=f"ruuvihub-{version}/{target.as_posix()}", recursive=False)
            # VERSION belongs to release artifacts, not source checkouts.
            info = tarfile.TarInfo(f"ruuvihub-{version}/VERSION")
            data = (version + "\n").encode("utf-8")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        os.replace(temporary_path, archive_path)
    finally:
        Path(temporary_path).unlink(missing_ok=True)
    return archive_path


def build_metadata(version: str, archive_path: Path, minimum_updater_version: str) -> bytes:
    """Create unsigned metadata for a locally built release archive."""
    if _VERSION.fullmatch(version) is None or _VERSION.fullmatch(minimum_updater_version) is None:
        raise ValueError("versions must be stable vX.Y.Z tags")
    if archive_path.name != f"ruuvihub-{version}.tar.gz":
        raise ValueError("archive name must match the release version")

    version_member = f"ruuvihub-{version}/VERSION"
    with tarfile.open(archive_path, "r:gz") as archive:
        members = [member for member in archive if member.name == version_member]
        if len(members) != 1 or not members[0].isfile() or members[0].size > 128:
            raise ValueError("archive must contain exactly one regular VERSION file")
        version_file = archive.extractfile(members[0])
        if version_file is None or version_file.read() != f"{version}\n".encode("ascii"):
            raise ValueError("archive VERSION does not match the release version")

    digest = hashlib.sha256()
    size = 0
    with archive_path.open("rb") as artifact:
        while chunk := artifact.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    metadata = {
        "version": version,
        "minimum_updater_version": minimum_updater_version,
        "artifacts": {
            "linux-arm64": {
                "name": archive_path.name,
                "size": size,
                "sha256": digest.hexdigest(),
            }
        },
    }
    return (json.dumps(metadata, indent=2) + "\n").encode("utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="Stable release tag, e.g. v1.1.0")
    parser.add_argument("--output-dir", type=Path, default=Path("."))
    parser.add_argument(
        "--minimum-updater-version",
        help="Also write release-metadata.json for this minimum updater version",
    )
    args = parser.parse_args()
    archive_path = build_archive(args.version, output_dir=args.output_dir)
    if args.minimum_updater_version:
        metadata = build_metadata(args.version, archive_path, args.minimum_updater_version)
        (args.output_dir / "release-metadata.json").write_bytes(metadata)
    print(archive_path)
