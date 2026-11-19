"""Switch a prepared Windows release without reverting persistent data."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from release_metadata import STABLE_VERSION
from windows_task import UserTask, replace_durably

_RECORD = "switch.json"


@contextmanager
def install_lock(base: Path):
    import msvcrt

    with (base / "update.lock").open("a+b") as lock:
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise RuntimeError("another RuuviHub installation operation is running") from error
        yield


class Activation:
    """Operate under the installer/updater lock."""

    def __init__(self, task: UserTask):
        self.task = task
        self.record = task.base / _RECORD

    def versions(self) -> tuple[str | None, str | None]:
        current = self.task.selected()
        try:
            transaction = json.loads(self.record.read_bytes())
        except FileNotFoundError:
            return current, None
        if not isinstance(transaction, dict) or set(transaction) != {"source", "target", "previous"}:
            raise ValueError("invalid Windows release switch record")
        for key, version in transaction.items():
            if key == "previous" and version is None:
                continue
            if not isinstance(version, str) or len(version) > 64 or not STABLE_VERSION.fullmatch(version):
                raise ValueError("invalid Windows release switch record")
        source, target, previous = (transaction[key] for key in ("source", "target", "previous"))
        if source == target or previous == source or current not in (source, target):
            raise ValueError("startup task does not match the recorded Windows release switch")
        return current, source if current == target else previous

    def _record_switch(self, source: str, target: str, previous: str | None) -> None:
        with NamedTemporaryFile(dir=self.record.parent, prefix=".switch-", delete=False) as file:
            path = Path(file.name)
            try:
                file.write(json.dumps({"source": source, "target": target, "previous": previous}).encode("ascii"))
                file.flush()
                os.fsync(file.fileno())
                file.close()
                replace_durably(path, self.record)
            finally:
                file.close()
                path.unlink(missing_ok=True)

    def first_start(self, version: str) -> None:
        if self.versions() != (None, None):
            raise ValueError("RuuviHub is already installed; use the updater")
        self.task.register(version)
        try:
            self.task.start()
            if not self.task.healthy(version):
                raise RuntimeError(f"release {version} did not become healthy")
        except (Exception, KeyboardInterrupt):
            try:
                self.task.stop()
            finally:
                self.task.remove()
            raise

    def switch(self, target: str, *, reverting: bool = False) -> None:
        source, previous = self.versions()
        if source is None or source == target:
            raise ValueError("no different installed release to select")
        self.task.release(target)
        if reverting:
            if target != previous:
                raise ValueError("revert must select the retained release")
        elif tuple(map(int, target[1:].split("."))) <= tuple(map(int, source[1:].split("."))):
            raise ValueError("use revert to select an older release")
        self._record_switch(source, target, previous)
        try:
            self.task.stop()
            self.task.select(target)
            self.task.start()
            if not self.task.healthy(target):
                raise RuntimeError(f"release {target} did not become healthy")
        except (Exception, KeyboardInterrupt) as error:
            try:
                self.task.stop()
                self.task.select(source)
                self.task.start()
                if not self.task.healthy(source):
                    self.task.stop()
                    raise RuntimeError(f"release {source} did not become healthy")
            except Exception as recovery_error:
                raise RuntimeError(
                    f"could not select {target} or restore {source}: {recovery_error}"
                ) from error
            raise RuntimeError(f"could not select {target}; restored {source}: {error}") from error
