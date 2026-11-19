"""Handle managed Pi backup and local health probes as the service user."""

import json
import os
from pathlib import Path
import shutil
import sqlite3
import ssl
import subprocess
import sys
from tempfile import mkdtemp
from urllib.request import build_opener, HTTPRedirectHandler, HTTPSHandler, ProxyHandler


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def healthy(base: Path, version: str) -> bool:
    settings = json.loads((base / "shared/settings.local.json").read_text(encoding="utf-8"))
    https = settings.get("https_enabled", True)
    port = settings.get("https_port", 8443) if https else 8080
    if type(port) is not int or not 1 <= port <= 65535:
        return False
    # This probe only speaks to a loopback listener with a possibly self-signed certificate.
    opener = build_opener(
        ProxyHandler({}), _NoRedirect(), HTTPSHandler(context=ssl._create_unverified_context()),
    )
    url = f"{'https' if https else 'http'}://localhost:{port}/healthz"
    with opener.open(url, timeout=2) as response:
        return json.loads(response.read(4096)) == {"status": "ok", "version": version}


def backup(base: Path) -> Path:
    """Make a private SQLite snapshot and settings copy while the service is stopped."""
    snapshot = (base / "shared/settings.local.json").read_bytes()
    db_path = Path(json.loads(snapshot)["db_path"])
    if not db_path.is_absolute():
        raise ValueError("managed updates require an absolute persistent database path")
    directory = Path(mkdtemp(prefix="before-update-", dir=base / "shared/backups"))
    try:
        (directory / "settings.local.json").write_bytes(snapshot)
        source = sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)
        try:
            destination = sqlite3.connect(directory / "ruuvi_data.db")
            try:
                source.backup(destination)
            finally:
                destination.close()
        finally:
            source.close()
        subprocess.run(["sync", "-f", str(directory)], check=True)
    except BaseException:
        shutil.rmtree(directory)
        raise
    return directory


if __name__ == "__main__":
    if os.geteuid() == 0:
        sys.exit("run the data helper as the service user")
    command, path, *args = sys.argv[1:]
    try:
        if command == "backup":
            print(backup(Path(path)))
        elif command == "health":
            sys.exit(0 if healthy(Path(path), *args) else 1)
        else:
            sys.exit("unknown data command")
    except (OSError, ValueError, KeyError, sqlite3.Error, subprocess.SubprocessError) as error:
        sys.exit(str(error) if command == "backup" else 1)
