#!/usr/bin/env python3
"""Manage releases on a bootstrapped RuuviHub Pi installation."""

import argparse
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import pwd
import re
import shlex
import shutil
import subprocess
import sys
import time
from tempfile import mkdtemp

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from pi_activation import PiActivation
from pi_release import latest_pi_artifact, stage_pi_release

_UNIT = "ruuvihub.service"
_TOOLS = Path(__file__).resolve().parent


def _systemctl(*args: str, timeout: int = 90) -> str:
    return subprocess.check_output(["systemctl", *args], text=True, timeout=timeout).strip()


def _require_trusted_path(path: Path) -> None:
    for location in (*reversed(path.parents), path):
        info = location.stat()
        if location.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError(f"managed path must be root-owned and not group/world writable: {location}")


@contextmanager
def _update_lock(base: Path):
    with (base / ".ruuvihub-update.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("another managed Pi operation is running") from None
        yield


def _service_user(base: Path) -> str:
    output = _systemctl(
        "show", "-p", "User", "-p", "WorkingDirectory", "-p", "ExecStart", "-p", "Environment", _UNIT,
    )
    properties = dict(line.split("=", 1) for line in output.splitlines())
    python = base / "current/venv/bin/python3"
    main = base / "current/src/main.py"
    command = properties.get("ExecStart", "")
    if properties.get("WorkingDirectory") != str(base / "current") or not command.startswith(
        f"{{ path={python} ; argv[]={python} {main} ;"
    ) or command.count("argv[]=") != 1:
        raise ValueError("systemd is not configured for this managed installation; bootstrap it first")
    settings = base / "shared/settings.local.json"
    if settings.is_symlink():
        settings = settings.readlink()
    bindings = [
        item.partition("=")[2] for item in shlex.split(properties.get("Environment", ""))
        if item.startswith("SETTINGS_PATH=")
    ]
    if not settings.is_absolute() or bindings != [str(settings)]:
        raise ValueError("systemd SETTINGS_PATH does not match the managed settings path")
    user = properties.get("User", "")
    if not user or pwd.getpwnam(user).pw_uid == 0:
        raise ValueError("the managed service must run as an unprivileged user")
    return user


def _as_user(user: str, *args: Path | str, timeout: int = 600) -> str:
    return subprocess.check_output(
        ["runuser", "-u", user, "--", "/usr/bin/python3", "-I", *map(str, args)], text=True, timeout=timeout,
    ).strip()


def _healthy(base: Path, user: str, version: str) -> bool:
    for _ in range(10):
        try:
            if _systemctl("is-active", _UNIT, timeout=4) == "active":
                _as_user(user, _TOOLS / "pi_update_data.py", "health", base, version, timeout=4)
                return True
        except (OSError, subprocess.SubprocessError):
            pass
        time.sleep(2)
    return False


def _prepare(base: Path, user: str, staging: Path, version: str) -> Path:
    # Give the service user read access to verified inputs, not write access to staged code.
    staging.chmod(0o755)
    environments = base / "shared/venvs"
    environment = Path(_as_user(user, _TOOLS / "prepare_pi_venv.py", environments, staging / "requirements.txt"))
    if (
        not environment.is_absolute() or environment.parent != environments
        or not re.fullmatch(r"managed-[0-9a-f]{64}-[a-z0-9_]{8}", environment.name)
        or environment.resolve(strict=True) != environment
    ):
        raise ValueError("dependency preparation returned an unexpected environment path")
    (staging / "venv").symlink_to(environment, target_is_directory=True)
    destination = base / "releases" / version
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"release {version} already exists in releases")
    staging.rename(destination)
    try:
        subprocess.run(["sync", "-f", str(destination)], check=True)
    except Exception:
        shutil.rmtree(destination)
        raise
    return destination


def manage(base: Path, command: str) -> None:
    if base != base.resolve(strict=True):
        raise ValueError("use the canonical managed installation path")
    _require_trusted_path(base / "releases")
    _require_trusted_path(base / "shared")
    _require_trusted_path(_TOOLS)
    user = _service_user(base)
    activation = PiActivation(
        base,
        stop=lambda: _systemctl("stop", _UNIT),
        start=lambda: _systemctl("start", _UNIT),
        healthy=lambda version: _healthy(base, user, version),
    )
    with _update_lock(base):
        active, previous = activation.versions()
        if command == "revert":
            activation.revert()
        else:
            key_path = _TOOLS / "release-public.hex"
            _require_trusted_path(key_path)
            key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(key_path.read_text(encoding="ascii")))
            artifact = latest_pi_artifact(key)
            if command == "status":
                print(f"Installed: {active}\nPrevious: {previous or 'none'}\nLatest verified: {artifact.version}")
                return
            if tuple(map(int, artifact.version[1:].split("."))) <= tuple(map(int, active[1:].split("."))):
                print(f"No newer verified release (installed {active}, latest {artifact.version}).")
                return
            if artifact.version == previous:
                raise ValueError("the latest release is already retained; use revert to reselect it")
            staging = stage_pi_release(artifact, base / "releases")
            try:
                # An interrupted activation can leave this verified version unselected.
                destination = base / "releases" / artifact.version
                if destination.is_dir() and not destination.is_symlink():
                    quarantine = Path(mkdtemp(prefix=".interrupted-", dir=destination.parent))
                    destination.rename(quarantine / destination.name)
                prepared = _prepare(base, user, staging, artifact.version)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)

            def backup():
                directory = _as_user(user, _TOOLS / "pi_update_data.py", "backup", base)
                print(f"Data backup: {directory}", flush=True)

            try:
                activation.update(artifact.version, backup)
            except Exception:
                if activation.versions()[0] == active:
                    shutil.rmtree(prepared)
                raise
        print(f"Selected {activation.versions()[0]}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", default=Path("/opt/ruuvihub"), type=Path)
    parser.add_argument("command", choices=["status", "update", "revert"])
    args = parser.parse_args()
    try:
        if os.geteuid() != 0:
            parser.error("run a managed command with sudo")
        manage(args.base_dir.absolute(), args.command)
    except (OSError, KeyError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        if isinstance(error, subprocess.CalledProcessError) and error.output:
            print(error.output, file=sys.stderr, end="")
        parser.exit(1, f"Pi updater: {error}\n")


if __name__ == "__main__":
    main()
