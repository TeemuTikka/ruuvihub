"""Install RuuviHub on a Pi or transfer an existing install to the signed updater."""

import argparse
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import shutil
import subprocess
from tempfile import TemporaryDirectory

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from pi_activation import _is_version, _sync_directory
from pi_handoff import handoff
from pi_release import _extract, latest_pi_artifact, stage_pi_release
from pi_update import _UNIT, _as_user, _healthy, _prepare, _require_trusted_path, _service_user, _systemctl, _update_lock

_TOOLS = (
    "pi_activation.py", "pi_release.py", "pi_update.py", "pi_update_data.py",
    "prepare_pi_venv.py", "release_metadata.py", "release-public.hex",
)


def _write_tools(tools: Path, payload: dict[str, bytes]) -> None:
    tools.mkdir(parents=True)
    for name in _TOOLS:
        (tools / name).write_bytes(payload[name])
        (tools / name).chmod(0o644)


def _key(payload: dict[str, bytes]) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(bytes.fromhex(payload["release-public.hex"].decode("ascii")))


def _owned_directory(path: Path, account: pwd.struct_passwd) -> None:
    path.mkdir(mode=0o700)
    os.chown(path, account.pw_uid, account.pw_gid)


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
    latest = latest_pi_artifact(_key(payload))
    print(f"Verified release channel ({latest.version}); preserving installed {release.name}.", flush=True)

    with TemporaryDirectory(prefix=".ruuvihub-bootstrap-", dir=base.parent) as temp:
        workspace = Path(temp)
        workspace.chmod(0o711)
        stage = workspace / "layout"
        tools = stage / "tools"
        _write_tools(tools, payload)
        helper = tools / "pi_update_data.py"
        settings = legacy / "shared/settings.local.json"
        _as_user(user, helper, "check-settings", settings)
        installed = stage / "releases" / release.name
        installed.mkdir(parents=True)
        _snapshot(user, helper, release, installed, workspace / "legacy.tar.gz")
        shared = stage / "shared"
        shared.mkdir()
        for name in ("backups", "venvs"):
            _owned_directory(shared / name, account)
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


def _enable_fresh_service(base: Path, user: str, version: str, system_units: Path) -> None:
    _require_trusted_path(system_units)
    unit = system_units / _UNIT
    with TemporaryDirectory(prefix=".ruuvihub-unit-", dir=system_units) as temp:
        candidate = Path(temp) / _UNIT
        with candidate.open("x", encoding="ascii") as output:
            output.write(
                "[Unit]\nDescription=RuuviHub BLE Scanner\n"
                "After=network.target bluetooth.target\nWants=bluetooth.target\n\n"
                f"[Service]\nUser={user}\nWorkingDirectory={base}/current\n"
                f"Environment=CONFIG_PATH={base}/shared/config.json\n"
                f"Environment=SETTINGS_PATH={base}/shared/data/settings.local.json\n"
                f"ExecStart={base}/current/venv/bin/python3 {base}/current/src/main.py\n"
                "Restart=on-failure\nRestartSec=10\n\n"
                "[Install]\nWantedBy=multi-user.target\n"
            )
            output.flush()
            os.fsync(output.fileno())
        os.link(candidate, unit)
    try:
        _sync_directory(system_units)
        _systemctl("daemon-reload")
        if _service_user(base) != user:
            raise RuntimeError("the new systemd unit does not match the managed layout")
        _systemctl("start", _UNIT)
        if not _healthy(base, user, version, tools=base / "tools"):
            raise RuntimeError(f"release {version} did not become healthy")
        _systemctl("enable", _UNIT)
    except (OSError, KeyError, ValueError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        try:
            _systemctl("stop", _UNIT)
            _systemctl("disable", _UNIT)
            unit.unlink()
            _sync_directory(system_units)
            _systemctl("daemon-reload")
        except (OSError, subprocess.SubprocessError) as cleanup:
            raise RuntimeError(f"install failed ({error}); systemd cleanup required: {cleanup}") from cleanup
        raise


def install(base: Path, user: str, payload: dict[str, bytes], system_units: Path = Path("/etc/systemd/system")) -> None:
    if base != base.resolve() or not re.fullmatch(r"/[A-Za-z0-9_./-]+", str(base)):
        raise ValueError("use a canonical absolute managed installation path")
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_-]*", user):
        raise ValueError("use an existing unprivileged service account")
    account = pwd.getpwnam(user)
    if account.pw_uid == 0:
        raise ValueError("use an existing unprivileged service account")
    _require_trusted_path(base.parent)
    if base.exists() or base.is_symlink():
        raise ValueError("the managed destination must not already exist")
    _check_legacy_processes()
    if _systemctl("show", "-p", "LoadState", "--value", _UNIT) != "not-found":
        raise ValueError("an existing RuuviHub service must be bootstrapped, not replaced")
    artifact = latest_pi_artifact(_key(payload))
    # Virtual environments must be created at their permanent path.
    base.mkdir(mode=0o755)
    with _update_lock(base):
        tools = base / "tools"
        _write_tools(tools, payload)
        releases = base / "releases"
        releases.mkdir()
        shared = base / "shared"
        shared.mkdir()
        for name in ("backups", "data", "venvs"):
            _owned_directory(shared / name, account)
        data = shared / "data"
        settings = data / "settings.local.json"
        (shared / "settings.local.json").symlink_to(settings)
        staging = stage_pi_release(artifact, releases)
        try:
            config = json.loads((staging / "config.json").read_bytes())
            if not isinstance(config, dict):
                raise ValueError("the verified release has no valid bootstrap configuration")
            config.update(
                db_path=str(data / "ruuvi_data.db"),
                log_path=str(data / "logs/ruuvihub.log"),
                tls_cert_path=str(data / "certs/ruuvihub.crt"),
                tls_key_path=str(data / "certs/ruuvihub.key"),
            )
            (shared / "config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
            _prepare(base, user, staging, artifact.version, tools=tools)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        (base / "current").symlink_to(f"releases/{artifact.version}", target_is_directory=True)
        subprocess.run(["sync", "-f", str(base)], check=True)
        _check_legacy_processes()
        _enable_fresh_service(base, user, artifact.version, system_units)
    print(f"Installed and started {artifact.version}.")


def main(payload: dict[str, bytes]) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--legacy-base", type=Path, default=Path("/home/pi/ruuvihub"))
    mode.add_argument("--fresh", action="store_true", help="install the latest signed release on a new Pi")
    parser.add_argument("--user", help="existing unprivileged service account for --fresh")
    parser.add_argument("--base-dir", type=Path, default=Path("/opt/ruuvihub"))
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("run the bootstrap with sudo")
    os.umask(0o022)
    try:
        if args.fresh:
            if not args.user:
                parser.error("--fresh requires --user")
            install(args.base_dir, args.user, payload)
        else:
            if args.user:
                parser.error("--user requires --fresh")
            bootstrap(args.legacy_base, args.base_dir, payload)
    except (OSError, KeyError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Pi bootstrap: {error}\n")
