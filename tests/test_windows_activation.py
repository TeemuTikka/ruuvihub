"""Verify Windows selection and recovery without changing a real scheduled task."""

import json
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from windows_activation import Activation, install_lock
from windows_task import UserTask

OLD = "v1.2.0"
NEW = "v1.3.0"


@pytest.fixture
def install(tmp_path, monkeypatch):
    task = UserTask(tmp_path, tmp_path / "data")
    for version in (OLD, NEW):
        release = tmp_path / "releases" / version
        (release / "runtime").mkdir(parents=True)
        (release / "src").mkdir()
        for file in ("runtime/python.exe", "src/main.py", "run.py"):
            (release / file).touch()
        (release / "VERSION").write_text(version)
    calls = []
    selection = {}

    def invoke(operation, action=None):
        if operation == "read":
            return json.dumps(selection) if selection else ""
        if operation in ("register", "select"):
            assert Path(action["Execute"]).parent.parent == Path(action["WorkingDirectory"])
            assert str(Path(action["WorkingDirectory"]) / "run.py") in action["Arguments"]
            selection.clear()
            selection.update(action)
        if operation == "remove":
            selection.clear()
        calls.append(operation)

    monkeypatch.setattr(task, "_invoke", invoke)
    monkeypatch.setattr(task, "healthy", lambda version: task.selected() == version)
    return task, calls, selection


def test_fresh_install_update_and_offline_revert(install):
    task, calls, _ = install
    activation = Activation(task)
    activation.first_start(OLD)
    assert calls == ["register", "start"]
    calls.clear()
    activation.switch(NEW)
    assert activation.versions() == (NEW, OLD)
    activation.switch(OLD, reverting=True)
    assert activation.versions() == (OLD, NEW)
    assert calls == ["stop", "select", "start"] * 2


def test_failed_update_restores_task_without_overwriting_data(install, monkeypatch):
    task, calls, _ = install
    activation = Activation(task)
    activation.first_start(OLD)
    task.data.mkdir()
    settings = task.data / "settings.local.json"
    settings.write_text("before update")

    def healthy(version):
        if version == NEW:
            settings.write_text("after attempted update")
            return False
        return task.selected() == OLD

    monkeypatch.setattr(task, "healthy", healthy)
    calls.clear()
    with pytest.raises(RuntimeError, match=f"restored {OLD}"):
        activation.switch(NEW)
    assert activation.versions() == (OLD, None)
    assert calls == ["stop", "select", "start"] * 2
    assert settings.read_text() == "after attempted update"


@pytest.mark.parametrize("changed", [False, True])
def test_recovers_state_after_interruption_at_task_write(install, monkeypatch, changed):
    task, _, _ = install
    Activation(task).first_start(OLD)
    select = task.select

    class PowerLoss(BaseException):
        pass

    def interrupt(version):
        if changed:
            select(version)
        raise PowerLoss()

    with monkeypatch.context() as patch:
        patch.setattr(task, "select", interrupt)
        with pytest.raises(PowerLoss):
            Activation(task).switch(NEW)
    assert Activation(task).versions() == ((NEW, OLD) if changed else (OLD, None))
    if changed:
        Activation(task).switch(OLD, reverting=True)
    assert task.selected() == OLD


def test_rejects_task_with_a_mismatched_runtime(install):
    task, _, selection = install
    Activation(task).first_start(OLD)
    selection["Execute"] = str(task.release(NEW) / "runtime/python.exe")
    with pytest.raises(ValueError, match="does not match"):
        Activation(task).versions()


@pytest.mark.skipif(os.name != "nt", reason="Windows byte-range lock")
def test_lock_rejects_another_handle_and_releases_on_exit(tmp_path):
    with install_lock(tmp_path):
        with pytest.raises(RuntimeError, match="another"):
            with install_lock(tmp_path):
                pytest.fail("the competing lock was granted")
    with install_lock(tmp_path):
        assert (tmp_path / "update.lock").exists()
