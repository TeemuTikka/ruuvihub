"""Handle managed Pi backup and local health probes as the service user."""

import json
import os
from pathlib import Path
import re
import resource
import shutil
import sqlite3
import ssl
import subprocess
import sys
import tarfile
from tempfile import mkdtemp
from urllib.request import build_opener, HTTPRedirectHandler, HTTPSHandler, ProxyHandler

MANAGED_ENVIRONMENT = re.compile(r"managed-[0-9a-f]{64}-[a-z0-9_]{8}")


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


def snapshot_release(release: Path, version: str) -> None:
    """Stream only app files, with no privileged reads of the legacy tree."""
    limit = 64 * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
    src = release / "src"
    if src.is_symlink() or not src.is_dir():
        raise ValueError("the legacy release must contain a real src directory")
    with tarfile.open(fileobj=sys.stdout.buffer, mode="w|gz", dereference=True) as archive:
        paths = [release / "VERSION", release / "requirements.txt", *sorted(src.rglob("*"))]
        for path in paths:
            parts = path.relative_to(release).parts
            if "__pycache__" in parts or path.suffix in {".pyc", ".pyo"}:
                continue
            if path.is_symlink() or not (path.is_dir() or path.is_file()):
                raise ValueError(f"unsupported entry in the legacy app: {path.name}")
            if path.is_file():
                archive.add(path, f"ruuvihub-{version}/{'/'.join(parts)}", recursive=False)


def check_settings(path: Path) -> None:
    settings = json.loads(path.read_bytes())
    paths = ["db_path", "log_path"]
    if settings.get("https_enabled", True):
        paths.extend(("tls_cert_path", "tls_key_path"))
    for field in paths:
        value = settings.get(field)
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError(f"set a persistent absolute {field} before bootstrapping")


def prune_environments(directory: Path, keep: set[str]) -> None:
    if directory.is_symlink() or directory.resolve() != directory:
        raise ValueError("refusing to prune an unexpected environment directory")
    for candidate in directory.iterdir():
        if (
            candidate.name not in keep
            and MANAGED_ENVIRONMENT.fullmatch(candidate.name)
            and not candidate.is_symlink()
            and candidate.is_dir()
        ):
            shutil.rmtree(candidate)


if __name__ == "__main__":
    if os.geteuid() == 0:
        sys.exit("run the data helper as the service user")
    command, path, *args = sys.argv[1:]
    try:
        if command == "backup":
            print(backup(Path(path)))
        elif command == "health":
            sys.exit(0 if healthy(Path(path), *args) else 1)
        elif command == "snapshot-release":
            snapshot_release(Path(path), *args)
        elif command == "check-settings":
            check_settings(Path(path))
        elif command == "prune-environments":
            prune_environments(Path(path), set(args))
        else:
            sys.exit("unknown data command")
    except (OSError, ValueError, KeyError, sqlite3.Error, subprocess.SubprocessError) as error:
        sys.exit(1 if command == "health" else str(error))
