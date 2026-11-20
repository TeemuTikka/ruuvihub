"""Exercise app startup and persisted reads/writes across a release and its predecessor."""

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

_TAG = "02:00:00:00:00:01"
_INTERVALS = (11, 12, 13)


async def _probe(source: Path, data: Path, phase: int) -> None:
    os.environ["CONFIG_PATH"] = str(data / "config.json")
    os.environ["SETTINGS_PATH"] = str(data / "settings.local.json")
    sys.path.insert(0, str(source / "src"))
    import main as app

    completed = False

    async def simulate_scan(settings, storage, _lock):
        nonlocal completed
        expected = list(range(1, phase + 1))
        readings = await storage.get_by_mac(_TAG)
        if sorted(row["temperature"] for row in readings) != expected:
            raise RuntimeError(f"phase {phase} could not read previous sensor records")
        previous = await settings.get()
        if phase and previous.scan_interval_seconds != _INTERVALS[phase - 1]:
            raise RuntimeError(f"phase {phase} could not read persisted settings")
        if phase < len(_INTERVALS):
            await storage.add(_TAG, {
                "mac": _TAG,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "temperature": phase + 1,
            })
            await settings.update(scan_interval_seconds=_INTERVALS[phase])
        completed = True

    async def disabled(*args):
        pass

    app.scan_loop = simulate_scan
    app.upload_loop = disabled
    app.history_backfill_loop = disabled
    app.web_server_loop = disabled
    await app.main()
    if not completed:
        raise RuntimeError(f"startup compatibility probe did not complete phase {phase}")


def _check(previous: Path, previous_python: Path, candidate: Path, candidate_python: Path) -> None:
    with TemporaryDirectory(prefix="ruuvihub-compatibility-") as directory:
        data = Path(directory)
        (data / "config.json").write_text(json.dumps({
            "db_path": str(data / "readings.db"),
            "log_path": str(data / "ruuvihub.log"),
            "db_retention_days": 0,
            "cloud_upload_enabled": False,
            "history_backfill_enabled": False,
        }), encoding="utf-8")
        probe = Path(__file__).resolve()
        versions = [(previous, previous_python), (candidate, candidate_python)] * 2
        for phase, (source, python) in enumerate(versions):
            print(f"Persistence check {phase + 1}/{len(versions)}: {source}", flush=True)
            subprocess.run(
                [str(python), "-I", str(probe), "--probe", str(source), str(data), str(phase)],
                check=True, cwd=data, timeout=60,
            )


if __name__ == "__main__":
    if sys.argv[1:2] == ["--probe"]:
        asyncio.run(_probe(Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4])))
    else:
        parser = argparse.ArgumentParser(description=__doc__)
        for argument in ("previous", "previous_python", "candidate", "candidate_python"):
            parser.add_argument(argument, type=Path)
        options = parser.parse_args()
        _check(*(path.absolute() for path in vars(options).values()))
