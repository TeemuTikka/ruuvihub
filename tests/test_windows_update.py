"""Verify the managed updater launches only a verified installer."""

from contextlib import nullcontext
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import windows_update


@pytest.fixture
def installed(tmp_path, monkeypatch):
    task = windows_update.UserTask(tmp_path, tmp_path / "data")
    active = tmp_path / "releases/v1.2.0"
    active.mkdir(parents=True)
    (active / "release-public.hex").write_text("ab" * 32)
    monkeypatch.setattr(task, "release", lambda version: active)
    state = ["v1.2.0", "v1.1.0"]
    monkeypatch.setattr(windows_update, "UserTask", lambda *args: task)
    monkeypatch.setattr(windows_update, "install_lock", lambda base: nullcontext())
    monkeypatch.setattr(windows_update, "_prune", lambda *args: None)
    monkeypatch.setattr(windows_update.Activation, "versions", lambda self: tuple(state))
    monkeypatch.setattr(windows_update, "windows_installer", lambda key: SimpleNamespace(
        version="v1.3.0", name="ruuvihub-v1.3.0-windows-x64.exe",
    ))
    return task, state


def test_update_verifies_download_before_invoking_controlled_setup(installed, monkeypatch):
    task, state = installed
    with monkeypatch.context() as patch:
        def bad_download(*args):
            raise ValueError("SHA-256 mismatch")

        patch.setattr(windows_update, "download_artifact", bad_download)
        patch.setattr(windows_update.subprocess, "run", lambda *a, **k: pytest.fail("unverified setup executed"))
        with pytest.raises(ValueError, match="SHA-256"):
            windows_update.manage(task.base, task.data, "update")

    def run(args, **kwargs):
        assert Path(args[0]).read_bytes() == b"verified"
        assert "/EXPECTEDCURRENT=v1.2.0" in args
        state[:] = ["v1.3.0", "v1.2.0"]

    monkeypatch.setattr(windows_update, "download_artifact", lambda artifact, path: path.write_bytes(b"verified"))
    monkeypatch.setattr(windows_update.subprocess, "run", run)
    windows_update.manage(task.base, task.data, "update")
    assert not list(task.base.glob("ruuvihub-download-*"))


def test_revert_uses_retained_version_without_network(installed, monkeypatch):
    task, _ = installed
    switched = []
    monkeypatch.setattr(windows_update, "windows_installer", lambda key: pytest.fail("revert touched network"))
    monkeypatch.setattr(windows_update.Activation, "switch", lambda self, version, *, reverting: switched.append(
        (version, reverting),
    ))
    windows_update.manage(task.base, task.data, "revert")
    assert switched == [("v1.1.0", True)]
