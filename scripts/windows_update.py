"""Check, install, or revert a managed Windows release."""

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from release_fetch import download_artifact
from release_metadata import STABLE_VERSION, _version_parts
from windows_activation import Activation, install_lock
from windows_release import windows_installer
from windows_task import UserTask


def _prune(task: UserTask, versions: tuple[str | None, str | None]) -> None:
    try:
        keep = {task.release(version) for version in versions if version is not None}
        for path in (task.base / "releases").iterdir():
            if path not in keep and STABLE_VERSION.fullmatch(path.name) and path.is_dir() and path.resolve() == path:
                shutil.rmtree(path)
    except (OSError, ValueError) as error:
        print(f"Warning: release selected, but cleanup did not complete: {error}", file=sys.stderr)


def manage(base: Path, data: Path, command: str) -> None:
    task = UserTask(base, data)
    activation = Activation(task)
    with install_lock(base):
        current, previous = activation.versions()
        if current is None:
            raise ValueError("no managed RuuviHub installation is selected")
        if command == "revert":
            if previous is None:
                raise ValueError("there is no previous release to select")
            activation.switch(previous, reverting=True)
            _prune(task, activation.versions())
            print(f"Selected {previous}.")
            return
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(
            (task.release(current) / "release-public.hex").read_text(encoding="ascii"),
        ))

    artifact = windows_installer(key)
    if command == "status":
        print(f"Installed: {current}\nPrevious: {previous or 'none'}\nLatest verified: {artifact.version}")
        return
    if _version_parts(artifact.version) <= _version_parts(current):
        print(f"No newer verified release (installed {current}, latest {artifact.version}).")
        return
    if artifact.version == previous:
        raise ValueError("the latest release is already retained; use revert to reselect it")
    with TemporaryDirectory(prefix="ruuvihub-download-", dir=base) as directory:
        executable = Path(directory) / artifact.name
        download_artifact(artifact, executable)
        # Setup takes the shared install lock and rejects a stale expected selection.
        subprocess.run(
            [str(executable), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
             f"/EXPECTEDCURRENT={current}"],
            check=True, timeout=600,
        )
    with install_lock(base):
        versions = activation.versions()
        if versions[0] != artifact.version:
            raise RuntimeError(f"setup did not select {artifact.version}")
        _prune(task, versions)
    print(f"Selected {artifact.version}.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["status", "update", "revert"])
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        release = Path(__file__).resolve().parent
        manage(release.parent.parent, args.data_dir.resolve(), args.command)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, f"RuuviHub updater: {error}\n")
