"""Move the systemd entry point to an already prepared managed Pi layout."""

import os
from pathlib import Path
import re
import subprocess
from tempfile import NamedTemporaryFile

from pi_activation import _sync_directory
from pi_update import _UNIT, _healthy, _require_trusted_path, _service_user, _systemctl

_DROP_IN = "99-ruuvihub-managed.conf"


def handoff(base: Path, user: str, version: str, system_units: Path = Path("/etc/systemd/system")) -> None:
    """Keep the original unit intact and remove the override if handoff fails."""
    if not base.is_absolute() or not re.fullmatch(r"/[A-Za-z0-9_./-]+", str(base)):
        raise ValueError("the managed installation path must be safe to write into a systemd unit")
    current = base / "current"
    python = current / "venv/bin/python3"
    main = current / "src/main.py"
    if not _healthy(base, user, version):
        raise RuntimeError(f"the original service is not healthy at {version}; handoff not attempted")

    _require_trusted_path(system_units)
    directory = system_units / f"{_UNIT}.d"
    directory.mkdir(mode=0o755, exist_ok=True)
    _require_trusted_path(directory)
    drop_in = directory / _DROP_IN
    override = (
        "[Service]\n"
        f"WorkingDirectory={current}\n"
        "ExecStart=\n"
        f"ExecStart={python} {main}\n"
    )
    installed = False
    try:
        with NamedTemporaryFile(dir=directory, prefix=".ruuvihub-handoff-") as candidate:
            candidate.write(override.encode("ascii"))
            candidate.flush()
            os.fchmod(candidate.fileno(), 0o644)
            os.fsync(candidate.fileno())
            os.link(candidate.name, drop_in)
            installed = True
        _sync_directory(directory)
        _sync_directory(system_units)
        _systemctl("daemon-reload")
        if _service_user(base) != user:
            raise RuntimeError("the override did not preserve the service user")
        _systemctl("restart", _UNIT)
        if not _healthy(base, user, version):
            raise RuntimeError(f"the managed service did not become healthy at {version}")
    except (KeyError, OSError, ValueError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        if not installed:
            raise
        try:
            drop_in.unlink()
            _sync_directory(directory)
            _systemctl("daemon-reload")
            _systemctl("restart", _UNIT)
            if not _healthy(base, user, version):
                raise RuntimeError("the restored legacy service is not healthy")
        except (OSError, RuntimeError, subprocess.SubprocessError) as restoration_error:
            raise RuntimeError(
                f"managed handoff failed ({error}); legacy recovery required: {restoration_error}"
            ) from restoration_error
        raise RuntimeError(f"managed handoff failed; restored the original unit: {error}") from error
