"""Download and stage an authenticated Pi release without activating it."""

from pathlib import Path
import re
import shutil
import tarfile
from tempfile import mkdtemp, TemporaryDirectory

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from release_fetch import download_artifact, fetch_artifact, latest_version
from release_metadata import Artifact, STABLE_VERSION

_CHUNK_SIZE = 1024 * 1024
_MAX_EXTRACTED_BYTES = 64 * _CHUNK_SIZE
_COMPONENT = re.compile(r"[a-zA-Z0-9_][a-zA-Z0-9._-]*")
_REQUIRED = {"VERSION", "config.json", "requirements.txt", "src/main.py"}


def _validate_artifact_name(artifact: Artifact) -> None:
    if not STABLE_VERSION.fullmatch(artifact.version) or artifact.name != f"ruuvihub-{artifact.version}.tar.gz":
        raise ValueError("unexpected Pi release archive name")


def latest_pi_artifact(public_key: Ed25519PublicKey) -> Artifact:
    artifact = fetch_artifact(public_key, latest_version(), "linux-arm64")
    _validate_artifact_name(artifact)
    return artifact


def _extract(archive_path: Path, destination: Path, version: str, *, required=_REQUIRED) -> None:
    names = set()
    total_size = 0
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive:
            root, _, name = member.name.partition("/")
            parts = name.split("/")
            if (
                root != f"ruuvihub-{version}"
                or not all(_COMPONENT.fullmatch(part) and not part.endswith(".") for part in parts)
                or any(part.lower() == "venv" for part in parts)
                or member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE)
                or name in names
            ):
                raise ValueError("unsafe entry in Pi release archive")
            names.add(name)
            total_size += member.size
            if member.size < 0 or total_size > _MAX_EXTRACTED_BYTES or len(names) > 4096:
                raise ValueError("Pi release archive is too large")
            target = destination.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output, length=_CHUNK_SIZE)
            target.chmod(0o644 | (member.mode & 0o111))
    if not required <= names or (destination / "VERSION").read_bytes() != f"{version}\n".encode("ascii"):
        raise ValueError("Pi release contents do not match the selected release")


def stage_pi_release(artifact: Artifact, staging_parent: Path) -> Path:
    """Stage an artifact returned by latest_pi_artifact under a trusted parent."""
    _validate_artifact_name(artifact)
    with TemporaryDirectory(prefix="ruuvihub-download-") as directory:
        archive_path = Path(directory) / "release.tar.gz"
        download_artifact(artifact, archive_path)

        destination = Path(mkdtemp(prefix=".ruuvihub-stage-", dir=staging_parent))
        try:
            _extract(archive_path, destination, artifact.version)
        except BaseException:
            shutil.rmtree(destination)
            raise
        return destination
