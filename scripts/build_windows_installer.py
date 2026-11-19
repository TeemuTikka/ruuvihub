"""Compile a Windows payload with Inno Setup and the publisher's public key."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

from build_release import _VERSION

_MODULES = (
    "release_fetch.py", "release_metadata.py", "windows_activation.py",
    "windows_release.py", "windows_task.py",
)


def build_installer(version: str, payload: Path, compiler: Path, output: Path, public_key: str) -> Path:
    if not _VERSION.fullmatch(version) or (payload / "VERSION").read_text(encoding="ascii").strip() != version:
        raise ValueError("the payload must match a stable release version")
    key = bytes.fromhex(public_key)
    if len(key) != 32:
        raise ValueError("provide a 32-byte Ed25519 public key")
    source = Path(__file__).resolve().parent
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="ruuvihub-setup-") as directory:
        staged = Path(directory) / "payload"
        shutil.copytree(payload, staged)
        shutil.copyfile(source / "windows_setup.py", staged / "setup.py")
        for module in _MODULES:
            shutil.copyfile(source / module, staged / "src" / module)
        (staged / "release-public.hex").write_text(key.hex() + "\n", encoding="ascii")
        subprocess.run(
            [str(compiler), f"/DReleaseVersion={version}", f"/DPayloadDirectory={staged}",
             f"/O{output}", str(source / "windows_installer.iss")],
            check=True, timeout=600,
        )
    installer = output / f"ruuvihub-{version}-windows-x64.exe"
    if not installer.is_file():
        raise ValueError("Inno Setup did not produce the expected installer")
    return installer


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument("payload", type=Path)
    parser.add_argument("--iscc", type=Path, default=Path("ISCC.exe"))
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    args = parser.parse_args()
    try:
        print(build_installer(args.version, args.payload, args.iscc, args.output_dir, os.environ["RELEASE_SIGNING_PUBLIC_KEY"]))
    except (OSError, KeyError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Windows installer build failed: {error}\n")
