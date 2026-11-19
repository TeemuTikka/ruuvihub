"""Install a verified payload from Inno Setup or remove its startup task."""

import argparse
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from release_metadata import STABLE_VERSION, _version_parts
from windows_activation import Activation, install_lock
from windows_release import verify_installer
from windows_task import UserTask, replace_durably


def install_payload(
    payload: Path, base: Path, data: Path, version: str, *, expected_current: str | None,
) -> None:
    if not STABLE_VERSION.fullmatch(version) or (payload / "VERSION").read_text(encoding="ascii").strip() != version:
        raise ValueError("the setup payload does not match the installer version")
    if base.is_relative_to(data) or data.is_relative_to(base):
        raise ValueError("application files and user data must be separate")
    base.mkdir(parents=True, exist_ok=True)
    with install_lock(base):
        task = UserTask(base, data)
        activation = Activation(task)
        current, previous = activation.versions()
        if current != expected_current:
            raise ValueError("the installed release changed; re-run the updater")
        if current is not None and _version_parts(version) <= _version_parts(current):
            raise ValueError("updates must install a newer version")
        if version == previous:
            raise ValueError("this release is already retained; use revert to select it")
        releases = base / "releases"
        releases.mkdir(exist_ok=True)
        destination = releases / version
        if destination.is_symlink() or destination.resolve() != destination:
            raise ValueError("unexpected link at the destination release")
        with TemporaryDirectory(prefix=".staging-", dir=releases) as directory:
            staging = Path(directory) / "payload"
            shutil.copytree(payload, staging, ignore=shutil.ignore_patterns("__pycache__"))
            if destination.exists():
                # This same version was never selected or was left by a failed switch.
                destination.rename(Path(directory) / "unselected")
            replace_durably(staging, destination)
        if current is None:
            activation.first_start(version)
        else:
            activation.switch(version)


def uninstall(base: Path, data: Path) -> None:
    with install_lock(base):
        task = UserTask(base, data)
        if task.selected() is not None:
            task.stop()
            task.remove()
        # Inno Setup removes the app directories after this process exits.


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["verify", "install", "uninstall"])
    parser.add_argument("--version")
    parser.add_argument("--installer", type=Path)
    parser.add_argument("--base-dir", type=Path)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--expected-current")
    args = parser.parse_args()
    payload = Path(__file__).resolve().parent
    try:
        if args.command == "verify":
            key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(
                (payload / "release-public.hex").read_text(encoding="ascii"),
            ))
            verify_installer(key, args.version, args.installer)
        elif args.command == "install":
            install_payload(
                payload, args.base_dir.resolve(), args.data_dir.resolve(), args.version,
                expected_current=args.expected_current,
            )
        else:
            uninstall(args.base_dir.resolve(), args.data_dir.resolve())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, f"RuuviHub setup: {error}\n")


if __name__ == "__main__":
    main()
