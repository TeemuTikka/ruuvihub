#!/usr/bin/env python3
"""Prepare the project virtual environment used by VS Code's app launches."""

from pathlib import Path
import os
import subprocess
import venv


def main():
    root = Path(__file__).resolve().parents[1]
    environment = root / ".venv"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(environment)
    subprocess.run([
        str(python), "-m", "pip", "install", "-r", str(root / "requirements.txt"),
    ], check=True)


if __name__ == "__main__":
    main()
