"""Exercise the packaged app with a disposable Docker volume and loopback port."""

import argparse
import json
import ssl
import subprocess
import time
from urllib.error import URLError
from urllib.request import urlopen
from uuid import uuid4


SEED = """
import asyncio
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/app/src")
from settings import SettingsStore
from storage import Storage

async def seed():
    store = SettingsStore()
    settings = await store.update(datetime_format="iso", history_backfill_enabled=False)
    storage = Storage(db_path=settings.db_path)
    mac = "02:00:00:00:00:01"
    await storage.add(mac, {
        "mac": mac,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "temperature": 21.25,
    })

asyncio.run(seed())
"""

SNAPSHOT = """
import hashlib
import json
import os
from pathlib import Path

settings = json.loads(Path(os.environ["SETTINGS_PATH"]).read_text())
assert Path(settings["db_path"]).is_file()
assert Path(settings["log_path"]).stat().st_size > 0
print(json.dumps({
    "datetime_format": settings["datetime_format"],
    "certificate": hashlib.sha256(Path(settings["tls_cert_path"]).read_bytes()).hexdigest(),
}))
"""


def docker(*args: str, capture: bool = False) -> str:
    result = subprocess.run(["docker", *args], check=True, text=True, capture_output=capture)
    return result.stdout.strip() if capture else ""


def wait_for_app(base_url: str, version: str, context: ssl.SSLContext) -> None:
    deadline = time.monotonic() + 75
    while True:
        try:
            with urlopen(f"{base_url}/healthz", context=context, timeout=3) as response:
                health = json.load(response)
            break
        except (URLError, TimeoutError, ConnectionError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)
    assert health == {"status": "ok", "version": f"v{version}"}, health
    with urlopen(base_url, context=context, timeout=5) as response:
        assert b"RuuviHub" in response.read()


def verify_image(image: str, version: str) -> None:
    name = f"ruuvihub-ha-test-{uuid4().hex}"
    context = ssl._create_unverified_context()  # Disposable self-signed certificate on loopback.
    docker("volume", "create", name, capture=True)
    try:
        for fresh_install in (True, False):
            docker(
                "run", "--detach", "--init", "--name", name,
                "--read-only", "--tmpfs", "/tmp",
                "--volume", f"{name}:/data", "--publish", "127.0.0.1::8443", image,
                capture=True,
            )
            try:
                address = docker("port", name, "8443/tcp", capture=True)
                base_url = f"https://{address}"
                wait_for_app(base_url, version, context)
                if fresh_install:
                    docker("exec", name, "python", "-c", SEED)
                    original = json.loads(docker("exec", name, "python", "-c", SNAPSHOT, capture=True))
                    assert original["datetime_format"] == "iso"
                else:
                    recreated = json.loads(docker("exec", name, "python", "-c", SNAPSHOT, capture=True))
                    assert recreated == original
                    with urlopen(f"{base_url}/api/current", context=context, timeout=5) as response:
                        assert "02:00:00:00:00:01" in response.read().decode().upper()
                docker("stop", name, capture=True)
                exit_code = docker("inspect", "--format", "{{.State.ExitCode}}", name, capture=True)
                assert exit_code in {"0", "130"}, f"Unexpected stop exit code {exit_code}"
            except Exception:
                docker("logs", name)
                raise
            finally:
                docker("rm", "--force", name, capture=True)
    finally:
        docker("volume", "rm", name, capture=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("version", help="Image version without the leading v")
    args = parser.parse_args()
    verify_image(args.image, args.version)
    print("Home Assistant app starts, serves HTTPS, and retains its data after replacement.")
