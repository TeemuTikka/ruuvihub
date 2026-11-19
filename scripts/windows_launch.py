"""Start one installed Windows release with settings outside the app directory."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile


def prepare_data(release: Path, data: Path) -> Path:
    data = data.resolve()
    release = release.resolve()
    if data.is_relative_to(release) or release.is_relative_to(data):
        raise ValueError("the data directory and app release must be separate")
    data.mkdir(parents=True, exist_ok=True)
    config = data / "config.json"
    if not config.exists():
        defaults = json.loads((release / "config.json").read_bytes())
        defaults.update(
            db_path=str(data / "ruuvi_data.db"),
            log_path=str(data / "logs" / "ruuvihub.log"),
            tls_cert_path=str(data / "certs" / "ruuvihub.crt"),
            tls_key_path=str(data / "certs" / "ruuvihub.key"),
        )
        with NamedTemporaryFile(dir=data, prefix=".config-", delete=False) as pending:
            path = Path(pending.name)
            try:
                pending.write((json.dumps(defaults, indent=2) + "\n").encode("utf-8"))
                pending.flush()
                os.fsync(pending.fileno())
                pending.close()
                try:
                    os.link(path, config)
                except FileExistsError:
                    pass
            finally:
                pending.close()
                path.unlink()
    return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    release = Path(__file__).resolve().parent
    try:
        if not args.data_dir.is_absolute():
            raise ValueError("the data directory must be absolute")
        if Path(sys.executable).resolve().parent != release / "runtime":
            raise ValueError("launch the app with Python from this release")
        data = prepare_data(release, args.data_dir)
    except (OSError, ValueError) as error:
        parser.exit(1, f"RuuviHub startup: {error}\n")
    os.environ["CONFIG_PATH"] = str(data / "config.json")
    os.environ["SETTINGS_PATH"] = str(data / "settings.local.json")
    os.environ.pop("RUUVIHUB_DISABLE_HTTPS", None)
    from main import main

    asyncio.run(main())
