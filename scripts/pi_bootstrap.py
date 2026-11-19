"""Transfer an existing Pi installation to the signed managed updater."""

import argparse
import os
from pathlib import Path
import pwd
import shlex
import subprocess
from tempfile import TemporaryDirectory

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from pi_activation import _is_version, _sync_directory
from pi_handoff import handoff
from pi_release import _extract, latest_pi_artifact
from pi_update import _UNIT, _as_user, _require_trusted_path, _systemctl, _update_lock

_TOOLS = (
    "pi_activation.py", "pi_release.py", "pi_update.py", "pi_update_data.py",
    "prepare_pi_venv.py", "release_metadata.py", "release-public.hex",
)


def _check_legacy_processes(proc: Path = Path("/proc")) -> None:
    for process in proc.iterdir():
        if not process.name.isdecimal():
            continue
        try:
            if process.stat().st_uid != 0:
                continue
            args = (process / "cmdline").read_bytes().split(b"\0")
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        scripts = {b"install.sh", b"update.sh", b"rollback.sh", b"uninstall.sh"}
        # A legacy script may be passed to bash directly or through sudo.
        if any(arg.rsplit(b"/", 1)[-1] in scripts for arg in args):
            raise RuntimeError("wait for the running legacy Pi management command to exit")


def _legacy_install(legacy: Path) -> tuple[str, Path, Path]:
    output = _systemctl(
        "show", "-p", "User", "-p", "WorkingDirectory", "-p", "ExecStart", "-p", "Environment", _UNIT,
    )
    properties = dict(line.split("=", 1) for line in output.splitlines())
    current = legacy / "current"
    python = legacy / "shared/venv/bin/python3"
    command = properties.get("ExecStart", "")
    settings = legacy / "shared/settings.local.json"
    env = shlex.split(properties.get("Environment", ""))
    if (
        properties.get("WorkingDirectory") != str(current)
        or not command.startswith(f"{{ path={python} ; argv[]={python} {current}/src/main.py ;")
        or command.count("argv[]=") != 1
        or [item for item in env if item.startswith("SETTINGS_PATH=")] != [f"SETTINGS_PATH={settings}"]
        or [item for item in env if item.startswith("CONFIG_PATH=")] != [f"CONFIG_PATH={legacy}/shared/config.json"]
    ):
        raise ValueError("the systemd unit does not match the legacy installation")
    user = properties.get("User", "")
    if not user or pwd.getpwnam(user).pw_uid == 0:
        raise ValueError("legacy service must run as an unprivileged user")
    release = current.resolve(strict=True)
    if release.parent != (legacy / "releases").resolve(strict=True) or not _is_version(release.name):
        raise ValueError("legacy current does not point to a stable installed release")
    environment = python.parent.parent.resolve(strict=True)
    if not (environment / "bin/python3").is_file() or environment.is_relative_to(release):
        raise ValueError("legacy service has no persistent Python environment")
    return user, release, environment


def _snapshot(user: str, helper: Path, release: Path, destination: Path, archive: Path) -> None:
    with archive.open("xb") as output:
        subprocess.run(
            ["runuser", "-u", user, "--", "/usr/bin/python3", "-I", str(helper),
             "snapshot-release", str(release), release.name],
            stdout=output, check=True, timeout=90,
        )
    _extract(archive, destination, release.name, required={"VERSION", "requirements.txt", "src/main.py"})


def bootstrap(legacy: Path, base: Path, payload: dict[str, bytes]) -> None:
    if legacy != legacy.resolve(strict=True) or base != base.resolve():
        raise ValueError("use canonical absolute installation paths")
    if base.exists() or base.is_relative_to(legacy) or legacy.is_relative_to(base):
        raise ValueError("the managed destination must be absent and separate from the legacy installation")
    _require_trusted_path(base.parent)
    _check_legacy_processes()
    user, release, environment = _legacy_install(legacy)
    account = pwd.getpwnam(user)
    key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(payload["release-public.hex"].decode("ascii")))
    latest = latest_pi_artifact(key)
    print(f"Verified release channel ({latest.version}); preserving installed {release.name}.", flush=True)

    with TemporaryDirectory(prefix=".ruuvihub-bootstrap-", dir=base.parent) as temp:
        workspace = Path(temp)
        workspace.chmod(0o711)
        stage = workspace / "layout"
        tools = stage / "tools"
        tools.mkdir(parents=True)
        for name in _TOOLS:
            (tools / name).write_bytes(payload[name])
            (tools / name).chmod(0o644)
        helper = tools / "pi_update_data.py"
        settings = legacy / "shared/settings.local.json"
        _as_user(user, helper, "check-settings", settings)
        installed = stage / "releases" / release.name
        installed.mkdir(parents=True)
        _snapshot(user, helper, release, installed, workspace / "legacy.tar.gz")
        shared = stage / "shared"
        shared.mkdir()
        for name in ("backups", "venvs"):
            directory = shared / name
            directory.mkdir(mode=0o700)
            os.chown(directory, account.pw_uid, account.pw_gid)
        (shared / "settings.local.json").symlink_to(settings)
        (shared / "legacy-venv").symlink_to(environment, target_is_directory=True)
        (installed / "venv").symlink_to(base / "shared/legacy-venv", target_is_directory=True)
        (stage / "current").symlink_to(f"releases/{release.name}", target_is_directory=True)
        stage.chmod(0o755)
        subprocess.run(["sync", "-f", str(stage)], check=True)
        # Publish only a complete layout; the legacy unit is still untouched.
        if base.exists() or base.is_symlink():
            raise ValueError("the managed destination appeared during preparation")
        stage.rename(base)
        _sync_directory(base.parent)
    with _update_lock(base):
        _check_legacy_processes()
        if _legacy_install(legacy) != (user, release, environment):
            raise RuntimeError("legacy installation changed during preparation; handoff not attempted")
        handoff(base, user, release.name)
    print(f"Managed installation selected {release.name}. The first app update is a separate command.")


def main(payload: dict[str, bytes]) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-base", type=Path, default=Path("/home/pi/ruuvihub"))
    parser.add_argument("--base-dir", type=Path, default=Path("/opt/ruuvihub"))
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("run the bootstrap with sudo")
    os.umask(0o022)
    try:
        bootstrap(args.legacy_base, args.base_dir, payload)
    except (OSError, KeyError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Pi bootstrap: {error}\n")
