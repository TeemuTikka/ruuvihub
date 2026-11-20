"""Build a versioned Windows x64 app directory for the installer."""

import argparse
from hashlib import file_digest
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from build_release import ROOT, _VERSION, collect_release_files

_APP_INPUTS = ("src", "config.json", "requirements.txt", "LICENSE", "THIRD_PARTY_NOTICES.md", "third_party_licenses")
_RUNTIME_FILE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*[A-Za-z0-9_]")


def _extract_runtime(archive: Path, digest: str, destination: Path) -> None:
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("provide the expected SHA-256 of the official Windows embeddable Python ZIP")
    with archive.open("rb") as source:
        if file_digest(source, "sha256").hexdigest() != digest:
            raise ValueError("embeddable Python SHA-256 does not match")
        source.seek(0)
        with ZipFile(source) as bundle:
            names = set()
            for entry in bundle.infolist():
                if not _RUNTIME_FILE.fullmatch(entry.filename) or entry.filename.lower() in names:
                    raise ValueError("unexpected filename in the embeddable Python ZIP")
                names.add(entry.filename.lower())
                with bundle.open(entry) as payload, (destination / entry.filename).open("xb") as file:
                    shutil.copyfileobj(payload, file)
    if not {"python.exe", "pythonw.exe", "license.txt"} <= names:
        raise ValueError("embeddable Python ZIP is missing required files")


def _prepare_launchers(runtime: Path) -> Path:
    console = runtime / "python-console.exe"
    # Keep the scheduled action path recognized by previously installed updaters.
    (runtime / "python.exe").rename(console)
    (runtime / "pythonw.exe").rename(runtime / "python.exe")
    return console


def _python_identity(executable: Path, cwd: Path) -> str:
    return subprocess.check_output(
        [str(executable), "-I", "-c",
         "import platform,sys; print(platform.machine(), *sys.version_info[:2])"],
        text=True, cwd=cwd, timeout=30,
    ).strip()


def build_payload(version: str, archive: Path, digest: str, output: Path, *, root: Path = ROOT) -> Path:
    if sys.platform != "win32" or platform.machine() != "AMD64" or sys.maxsize <= 2**32:
        raise ValueError("build the payload with 64-bit x64 Python on Windows")
    if not _VERSION.fullmatch(version):
        raise ValueError("version must be a stable vX.Y.Z tag")
    output = output.resolve()
    destination = output / f"ruuvihub-{version}-windows-x64"
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    files = collect_release_files(
        root, [(root / name, name) for name in _APP_INPUTS] + [(root / "scripts/windows_launch.py", "run.py")],
    )
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".windows-payload-", dir=output) as temp:
        staged = Path(temp) / "payload"
        runtime = staged / "runtime"
        runtime.mkdir(parents=True)
        _extract_runtime(archive, digest, runtime)
        embedded = _prepare_launchers(runtime)
        major, minor = sys.version_info[:2]
        if _python_identity(embedded, runtime) != f"AMD64 {major} {minor}":
            raise ValueError("the embedded Python must match the build Python architecture and major/minor version")
        stem = f"python{major}{minor}"
        if not (runtime / f"{stem}.zip").is_file() or not (runtime / f"{stem}._pth").is_file():
            raise ValueError("embeddable Python ZIP has no expected standard library or path configuration")
        (runtime / f"{stem}._pth").write_text(
            f"{stem}.zip\n.\nLib\\site-packages\n..\\src\nimport site\n", encoding="ascii",
        )
        for source, relative in files:
            target = staged / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        (staged / "VERSION").write_bytes(f"{version}\n".encode("ascii"))
        subprocess.run(
            [sys.executable, "-I", "-m", "pip", "install", "--require-hashes", "--only-binary=:all:",
             "--no-compile", "--target", str(runtime / "Lib/site-packages"), "-r", str(staged / "requirements.txt")],
            check=True, timeout=600,
        )
        subprocess.run(
            [str(embedded), "-I", "-B", "-c",
             f"import main, sqlite3, ssl; from version import get_version; assert get_version() == {version!r}"],
            cwd=runtime, check=True, timeout=60,
        )
        staged.rename(destination)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument("--python-zip", type=Path, required=True)
    parser.add_argument("--python-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    args = parser.parse_args()
    try:
        print(build_payload(args.version, args.python_zip, args.python_sha256, args.output_dir))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Windows payload build failed: {error}\n")
