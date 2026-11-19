"""Package the host-side Pi bootstrap with its trusted public key."""

import argparse
import os
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

_MODULES = (
    "pi_activation.py", "pi_bootstrap.py", "pi_handoff.py", "pi_release.py", "pi_update.py",
    "pi_update_data.py", "prepare_pi_venv.py", "release_fetch.py", "release_metadata.py",
)
_ENTRY = """\
import sys
from zipfile import ZipFile
from pi_bootstrap import _TOOLS, main

with ZipFile(sys.argv[0]) as bundle:
    payload = {name: bundle.read(name) for name in _TOOLS}
main(payload)
"""


def build_bootstrap(output: Path, public_key: str) -> None:
    public_key = bytes.fromhex(public_key)
    if len(public_key) != 32:
        raise ValueError("the Pi bootstrap requires a 32-byte Ed25519 public key")
    source = Path(__file__).resolve().parent
    with ZipFile(output, "x", compression=ZIP_DEFLATED) as bundle:
        bundle.writestr("__main__.py", _ENTRY)
        bundle.writestr("release-public.hex", public_key.hex() + "\n")
        for name in _MODULES:
            bundle.write(source / name, name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        build_bootstrap(args.output, os.environ["RELEASE_SIGNING_PUBLIC_KEY"])
    except (OSError, KeyError, ValueError) as error:
        parser.exit(1, f"Could not build Pi bootstrap: {error}\n")
