#!/usr/bin/env python3
"""Build the same release archive locally and in CI, without local runtime state."""

import argparse
import io
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
_REQUIRED_FILES = (
    "src/main.py", "scripts/install.sh", "scripts/update.sh", "scripts/rollback.sh",
    "scripts/uninstall.sh", "scripts/lib.sh", "scripts/ruuvihub.service.template",
    "third_party_licenses/Chart.js-MIT.txt", "third_party_licenses/kurkle-color-MIT.txt",
)


def build_archive(version: str, root: Path = ROOT, output_dir: Path = Path(".")) -> Path:
    if re.fullmatch(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version) is None:
        raise ValueError("version must be a stable vX.Y.Z tag")
    for name in _REQUIRED_FILES:
        if not (root / name).is_file():
            raise FileNotFoundError(f"Missing required release input: {name}")
    sources = [(root / name, name) for name in _REQUIRED]
    sources.append((root / "tests/test_connection.py", "scripts/test_connection.py"))
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="Stable release tag, e.g. v1.1.0")
    parser.add_argument("--output-dir", type=Path, default=Path("."))
    args = parser.parse_args()
    print(build_archive(args.version, output_dir=args.output_dir))
