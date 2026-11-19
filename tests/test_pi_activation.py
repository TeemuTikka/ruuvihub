"""Verify managed Pi switches without using a running installation."""

import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import pi_activation
from pi_activation import ActivationError, PiActivation

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Pi switch uses Linux directory fsync")

OLD = "v1.0.0"
CURRENT = "v1.1.0"
NEW = "v1.2.0"


@pytest.fixture
def install(tmp_path):
    base = tmp_path / "hub"
    for version in (OLD, CURRENT, NEW):
        release = base / "releases" / version
        environment = base / "shared" / "venvs" / version
        (release / "src").mkdir(parents=True)
        (release / "src/main.py").write_text(version)
        (release / "VERSION").write_text(version + "\n")
        (environment / "bin").mkdir(parents=True)
        (environment / "bin/python3").write_text(version)
        (release / "venv").symlink_to(environment, target_is_directory=True)
    (base / "current").symlink_to(f"releases/{CURRENT}", target_is_directory=True)
    pi_activation._write_switch(base, OLD, CURRENT, None)

    events = []
    unhealthy = set()

    def selected():
        return (
            (base / "current/src/main.py").read_text(),
            (base / "current/venv/bin/python3").read_text(),
        )

    def healthy(version):
        events.append(("health", version))
        return selected() == (version, version) and version not in unhealthy

    def start():
        app, python = selected()
        assert app == python
        events.append(("start", app))

    def controller():
        return PiActivation(base, stop=lambda: events.append(("stop",)), start=start, healthy=healthy)

    return base, controller, selected, events, unhealthy


def test_update_and_revert_switch_code_and_python_together(install):
    _, controller, selected, events, _ = install
    controller().update(NEW, lambda: events.append(("backup", selected())))
    assert events == [
        ("stop",), ("backup", (CURRENT, CURRENT)), ("start", NEW), ("health", NEW),
    ]
    assert controller().versions() == (NEW, CURRENT)
    controller().revert()
    assert controller().versions() == (CURRENT, NEW)
    assert selected() == (CURRENT, CURRENT)


@pytest.mark.parametrize("operation", ["update", "revert"])
def test_failed_health_restores_prior_selection_without_restoring_data(install, operation):
    base, controller, selected, _, unhealthy = install
    target = NEW if operation == "update" else OLD
    unhealthy.add(target)
    readings = base / "shared/readings"
    readings.write_text("current readings")
    with pytest.raises(ActivationError, match=f"restored {CURRENT}"):
        if operation == "update":
            controller().update(target, lambda: readings.write_text("newer readings"))
        else:
            controller().revert()
    assert controller().versions() == (CURRENT, OLD)
    assert selected() == (CURRENT, CURRENT)
    assert readings.read_text() == ("newer readings" if operation == "update" else "current readings")


def test_failed_backup_restarts_current_without_selecting_target(install):
    _, controller, selected, events, _ = install

    def backup():
        raise OSError("no space left")

    with pytest.raises(ActivationError, match="no space left"):
        controller().update(NEW, backup)
    assert events == [("stop",), ("start", CURRENT), ("health", CURRENT)]
    assert selected() == (CURRENT, CURRENT)
    assert controller().versions() == (CURRENT, OLD)


@pytest.mark.parametrize("replaced", [False, True])
def test_interrupted_switch_can_recover_from_persisted_selection(install, monkeypatch, replaced):
    base, controller, selected, _, _ = install
    real_replace = os.replace

    class PowerLoss(BaseException):
        pass

    def replace(src, dest):
        if dest != base / "current":
            return real_replace(src, dest)
        if replaced:
            real_replace(src, dest)
        raise PowerLoss()

    with monkeypatch.context() as patch:
        patch.setattr(pi_activation.os, "replace", replace)
        with pytest.raises(PowerLoss):
            controller().update(NEW, lambda: None)
    active, previous = (NEW, CURRENT) if replaced else (CURRENT, OLD)
    assert selected() == (active, active)
    assert controller().versions() == (active, previous)
    controller().revert()
    assert selected() == (previous, previous)


def test_refuses_to_switch_to_an_unprepared_release(install):
    base, controller, _, events, _ = install
    (base / f"releases/{NEW}/venv").unlink()
    with pytest.raises((OSError, ValueError)):
        controller().update(NEW, lambda: None)
    assert events == []


def test_revert_uses_pinned_legacy_environment_outside_managed_root(install):
    base, controller, selected, _, _ = install
    release_link = base / f"releases/{CURRENT}/venv"
    original = base.parent / "legacy/shared/venvs" / CURRENT
    original.parent.mkdir(parents=True)
    release_link.resolve().rename(original)
    release_link.unlink()
    release_link.symlink_to(original)
    with pytest.raises(ValueError, match="no prepared Python environment"):
        controller().versions()
    binding = base / "shared/legacy-venv"
    binding.symlink_to(original)
    release_link.unlink()
    release_link.symlink_to(binding)
    controller().update(NEW, lambda: None)
    controller().revert()
    assert selected() == (CURRENT, CURRENT)
    assert release_link.resolve() == original


def test_both_unhealthy_releases_require_operator_recovery(install):
    _, controller, selected, _, unhealthy = install
    unhealthy.update({CURRENT, NEW})
    with pytest.raises(ActivationError, match="operator recovery required"):
        controller().update(NEW, lambda: None)
    assert selected() == (CURRENT, CURRENT)
