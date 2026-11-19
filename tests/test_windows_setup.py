"""Verify that installer payloads cannot replace the selected release or its data."""

from contextlib import nullcontext
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import windows_setup


@pytest.fixture
def setup(tmp_path, monkeypatch):
    base = tmp_path / "app"
    data = tmp_path / "data"
    data.mkdir()
    (data / "settings.local.json").write_text("persisted settings")
    payload = tmp_path / "payload"
    (payload / "runtime").mkdir(parents=True)
    (payload / "runtime/python.exe").write_text("new Python")
    (payload / "VERSION").write_text("v1.3.0")
    actions = []

    class Controller:
        selected = (None, None)

        def versions(self):
            return self.selected

        def first_start(self, version):
            assert (base / "releases" / version / "runtime/python.exe").read_text() == "new Python"
            actions.append(("first", version))

        def switch(self, version):
            assert (base / "releases" / version / "runtime/python.exe").read_text() == "new Python"
            actions.append(("switch", version))

    controller = Controller()
    monkeypatch.setattr(windows_setup, "Activation", lambda task: controller)
    monkeypatch.setattr(windows_setup, "install_lock", lambda base: nullcontext())
    return base, data, payload, controller, actions


def test_fresh_setup_publishes_payload_and_preserves_existing_data(setup):
    base, data, payload, _, actions = setup
    windows_setup.install_payload(payload, base, data, "v1.3.0", expected_current=None)
    assert actions == [("first", "v1.3.0")]
    assert (data / "settings.local.json").read_text() == "persisted settings"


def test_controlled_update_refuses_stale_selection_then_installs_new_version(setup):
    base, data, payload, controller, actions = setup
    controller.selected = ("v1.2.0", None)
    retained = base / "releases/v1.2.0"
    retained.mkdir(parents=True)
    (retained / "VERSION").write_text("v1.2.0")
    with pytest.raises(ValueError, match="installed release changed"):
        windows_setup.install_payload(payload, base, data, "v1.3.0", expected_current="v1.1.0")
    assert not actions and not (base / "releases/v1.3.0").exists()
    windows_setup.install_payload(payload, base, data, "v1.3.0", expected_current="v1.2.0")
    assert actions == [("switch", "v1.3.0")]
    assert (retained / "VERSION").read_text() == "v1.2.0"
    assert (data / "settings.local.json").read_text() == "persisted settings"
