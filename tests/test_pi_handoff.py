"""Verify that the systemd handoff is reversible without modifying the legacy unit."""

from pathlib import Path
import sys

import pytest

if sys.platform != "linux":
    pytest.skip("systemd handoff requires Linux", allow_module_level=True)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import pi_handoff


@pytest.fixture
def unit(tmp_path, monkeypatch):
    base = tmp_path / "managed"
    units = tmp_path / "system"
    units.mkdir()
    legacy = units / "ruuvihub.service"
    legacy.write_text("[Service]\nExecStart=/original/python /original/main.py\n")
    override = units / "ruuvihub.service.d/99-ruuvihub-managed.conf"
    calls = []
    monkeypatch.setattr(pi_handoff, "_require_trusted_path", lambda path: None)
    monkeypatch.setattr(pi_handoff, "_service_user", lambda path: "nobody")
    monkeypatch.setattr(pi_handoff, "_systemctl", lambda *args: calls.append((args[0], override.is_file())))
    return base, units, legacy, override, calls


def test_handoff_preserves_legacy_unit_and_checks_same_version(unit, monkeypatch):
    base, units, legacy, override, calls = unit
    before = legacy.read_bytes()

    def healthy(path, user, version, *, tools):
        assert (path, user, version) == (base, "nobody", "v1.2.3")
        assert tools == base / "tools"
        calls.append(("health", override.is_file()))
        return True

    monkeypatch.setattr(pi_handoff, "_healthy", healthy)
    pi_handoff.handoff(base, "nobody", "v1.2.3", units)
    assert calls == [("health", False), ("daemon-reload", True), ("restart", True), ("health", True)]
    assert legacy.read_bytes() == before
    assert override.read_text() == (
        f"[Service]\nWorkingDirectory={base}/current\nExecStart=\n"
        f"ExecStart={base}/current/venv/bin/python3 {base}/current/src/main.py\n"
    )


def test_unhealthy_managed_service_recovers_original_unit(unit, monkeypatch):
    base, units, legacy, override, calls = unit
    before = legacy.read_bytes()
    monkeypatch.setattr(pi_handoff, "_healthy", lambda *args, **kwargs: not override.exists())
    with pytest.raises(RuntimeError, match="restored the original unit"):
        pi_handoff.handoff(base, "nobody", "v1.2.3", units)
    assert not override.exists()
    assert legacy.read_bytes() == before
    assert calls == [("daemon-reload", True), ("restart", True), ("daemon-reload", False), ("restart", False)]


def test_handoff_never_overwrites_an_existing_override(unit, monkeypatch):
    base, units, _, override, calls = unit
    override.parent.mkdir()
    override.write_text("existing")
    monkeypatch.setattr(pi_handoff, "_healthy", lambda *args, **kwargs: True)
    with pytest.raises(FileExistsError):
        pi_handoff.handoff(base, "nobody", "v1.2.3", units)
    assert override.read_text() == "existing"
    assert not calls
