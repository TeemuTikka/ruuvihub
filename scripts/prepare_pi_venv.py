#!/usr/bin/env python3
"""Prepare a locked Python environment for the managed Pi updater."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import tempfile

_READY = ".ruuvihub-environment"


def _environment_key(requirements: bytes) -> str:
    if sys.prefix != sys.base_prefix:
        raise ValueError("use the system Python, not an installed virtual environment")
    executable = Path(sys.executable).resolve()
    with executable.open("rb") as binary:
        python_hash = hashlib.file_digest(binary, "sha256").hexdigest()
    identity = {
        "executable": str(executable),
        "python_sha256": python_hash,
        "version": sys.version,
        "abi": sysconfig.get_config_var("SOABI"),
        "platform": sysconfig.get_platform(),
        "requirements_sha256": hashlib.sha256(requirements).hexdigest(),
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()


def prepare_environment(environments: Path, requirements: Path) -> Path:
    """Return a ready environment without modifying or removing existing ones."""
    lock = requirements.read_bytes()
    identity = _environment_key(lock)
    environments = environments.resolve()
    environments.mkdir(parents=True, exist_ok=True)
    prefix = f"managed-{identity}-"
    for candidate in sorted(environments.glob(f"{prefix}*")):
        if candidate.is_symlink() or not (candidate / "bin/python3").is_file():
            continue
        marker = candidate / _READY
        if marker.is_file() and marker.read_bytes() == identity.encode("ascii"):
            return candidate

    # Venv scripts embed absolute paths, so build at the final path and mark it ready last.
    environment = Path(tempfile.mkdtemp(prefix=prefix, dir=environments))
    try:
        subprocess.run(
            [sys.executable, "-m", "venv", str(environment)], check=True, stdout=sys.stderr
        )
        snapshot = environment / "requirements.txt"
        snapshot.write_bytes(lock)
        subprocess.run(
            [
                str(environment / "bin/python3"), "-m", "pip", "install",
                "--require-hashes", "--only-binary=:all:", "-r", str(snapshot),
            ],
            check=True,
            stdout=sys.stderr,
        )
        (environment / _READY).write_bytes(identity.encode("ascii"))
    except BaseException:
        shutil.rmtree(environment)
        raise
    return environment


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("environments", type=Path)
    parser.add_argument("requirements", type=Path)
    args = parser.parse_args()
    try:
        environment = prepare_environment(args.environments, args.requirements)
        subprocess.run(["sync", "-f", str(environment)], check=True)
        print(environment)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Could not prepare Pi Python environment: {error}\n")
