"""Start one installed Windows release with settings outside the app directory."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile
from threading import Event
import webbrowser


def _dashboard_url(settings_path: Path) -> str:
    try:
        settings = json.loads(settings_path.read_bytes())
    except (OSError, ValueError):
        settings = {}
    if not isinstance(settings, dict):
        settings = {}
    if settings.get("https_enabled") is False:
        return "http://127.0.0.1:8080/"
    port = settings.get("https_port", 8443)
    if type(port) is not int or not 1 <= port <= 65535:
        port = 8443
    return f"https://127.0.0.1:{port}/"


def _tray_icon(settings_path: Path):
    from PIL import Image, ImageDraw
    from pystray import Icon, Menu, MenuItem

    image = Image.new("RGBA", (64, 64))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2, 2, 61, 61), radius=13, fill="#17675e")
    draw.line(((12, 42), (23, 30), (32, 36), (49, 18)), fill="white", width=5, joint="curve")

    def open_dashboard(_icon, _item):
        webbrowser.open(_dashboard_url(settings_path))

    return Icon("RuuviHub", image, "RuuviHub", Menu(
        MenuItem("Open dashboard", open_dashboard, default=True),
    ))


def _detach_console() -> None:
    import ctypes

    sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")
    # Keep the existing task action so the v2.0.0 updater can recognize it.
    ctypes.windll.kernel32.FreeConsole()


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
    _detach_console()
    from main import main

    tray = None
    ready = Event()

    def show(icon):
        try:
            icon.visible = True
        finally:
            ready.set()

    try:
        tray = _tray_icon(data / "settings.local.json")
        tray.run_detached(setup=show)
        asyncio.run(main())
    finally:
        if tray is not None and ready.wait(timeout=5):
            tray.stop()
