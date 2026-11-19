"""Switch a prepared Pi release and its Python environment as one unit."""

from collections.abc import Callable
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from release_metadata import STABLE_VERSION

_STATE = ".ruuvihub-switch.json"


def _is_version(value: object) -> bool:
    return isinstance(value, str) and len(value) <= 64 and STABLE_VERSION.fullmatch(value) is not None


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_switch(base: Path, source: str, target: str, previous: str | None) -> None:
    with TemporaryDirectory(prefix=".ruuvihub-switch-", dir=base) as directory:
        path = Path(directory) / "state"
        with path.open("x", encoding="utf-8") as file:
            json.dump({"source": source, "target": target, "previous": previous}, file)
            file.flush()
            os.fsync(file.fileno())
        os.replace(path, base / _STATE)
        _sync_directory(base)


def _select(base: Path, version: str) -> None:
    with TemporaryDirectory(prefix=".ruuvihub-current-", dir=base) as directory:
        link = Path(directory) / "current"
        # A symlink's relative target is interpreted at its final location.
        link.symlink_to(f"releases/{version}", target_is_directory=True)
        _sync_directory(link.parent)
        os.replace(link, base / "current")
        _sync_directory(base)


class ActivationError(RuntimeError):
    """The service could not be activated or safely restored."""


class PiActivation:
    """Operate on a managed installation under the caller's exclusive update lock."""

    def __init__(
        self, base: Path, *, stop: Callable[[], None], start: Callable[[], None],
        healthy: Callable[[str], bool],
    ):
        self.base = base.resolve(strict=True)
        self.stop = stop
        self.start = start
        self.healthy = healthy

    def _release(self, version: str) -> Path:
        if not _is_version(version):
            raise ValueError("invalid Pi release version")
        releases = self.base / "releases"
        path = releases / version
        if releases.is_symlink() or path.is_symlink() or not (path / "src/main.py").is_file():
            raise ValueError(f"release {version} is not installed")
        if (path / "VERSION").read_text(encoding="ascii").strip() != version:
            raise ValueError(f"release {version} has an unexpected VERSION")

        link = path / "venv"
        shared = self.base / "shared"
        environment = link.resolve(strict=True)
        if (
            not link.is_symlink()
            or not (link / "bin/python3").is_file()
            or not (environment == shared / "venv" or environment.is_relative_to(shared / "venvs"))
        ):
            raise ValueError(f"release {version} has no prepared Python environment")
        return path

    def versions(self) -> tuple[str, str | None]:
        current = self.base / "current"
        if not current.is_symlink():
            raise ValueError("no managed Pi installation is selected")
        active = current.resolve(strict=True)
        if self._release(active.name) != active:
            raise ValueError("current points outside the installed releases")
        try:
            state = json.loads((self.base / _STATE).read_bytes())
        except FileNotFoundError:
            return active.name, None
        if (
            not isinstance(state, dict)
            or set(state) != {"source", "target", "previous"}
            or not _is_version(state["source"])
            or not _is_version(state["target"])
            or state["source"] == state["target"]
            or (state["previous"] is not None and not _is_version(state["previous"]))
        ):
            raise ValueError("invalid Pi release switch state")
        if state["source"] == active.name:
            previous = state["previous"]
        elif state["target"] == active.name:
            previous = state["source"]
        else:
            raise ValueError("Pi release switch state does not match current")
        if previous == active.name:
            raise ValueError("invalid previous Pi release")
        return active.name, previous

    def update(self, target: str, backup: Callable[[], None]) -> None:
        """Select a durable prepared release after backing up with the service stopped."""
        source, previous = self.versions()
        self._release(target)
        if tuple(map(int, target[1:].split("."))) <= tuple(map(int, source[1:].split("."))):
            raise ValueError("Pi updates must select a newer release; use revert to roll back")
        self._switch(source, target, previous, backup)

    def revert(self) -> None:
        source, previous = self.versions()
        if previous is None:
            raise ValueError("no previous Pi release is recorded")
        self._release(previous)
        self._switch(source, previous, previous)

    def _switch(
        self, source: str, target: str, previous: str | None,
        backup: Callable[[], None] | None = None,
    ) -> None:
        _write_switch(self.base, source, target, previous)
        self.stop()
        try:
            if backup is not None:
                backup()
            _select(self.base, target)
            self.start()
            if not self.healthy(target):
                raise ActivationError(f"release {target} did not become healthy")
        except Exception as error:
            try:
                if (self.base / "current").resolve(strict=True).name != source:
                    self.stop()
                    _select(self.base, source)
                self.start()
                if not self.healthy(source):
                    raise ActivationError(f"release {source} did not become healthy")
            except Exception:
                raise ActivationError(
                    f"could not select {target} or restore {source}; operator recovery required"
                ) from error
            raise ActivationError(f"could not select {target}; restored {source}: {error}") from error
