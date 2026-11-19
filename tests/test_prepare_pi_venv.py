"""Verify that Pi environment preparation never mutates a ready environment."""

from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import prepare_pi_venv


@pytest.fixture
def preparation(tmp_path, monkeypatch):
    executable = tmp_path / "system-python"
    executable.write_bytes(b"system Python build")
    monkeypatch.setattr(prepare_pi_venv.sys, "executable", str(executable))
    monkeypatch.setattr(prepare_pi_venv.sys, "prefix", sys.base_prefix)
    calls = []

    def run(command, *, check, stdout):
        assert check
        calls.append(command)
        if command[2] == "venv":
            binary = Path(command[3]) / "bin/python3"
            binary.parent.mkdir()
            binary.touch()
        else:
            assert command[1:6] == ["-m", "pip", "install", "--require-hashes", "--only-binary=:all:"]
            assert Path(command[-1]).read_bytes() == requirements.read_bytes()

    monkeypatch.setattr(prepare_pi_venv.subprocess, "run", run)
    requirements = tmp_path / "requirements.txt"
    requirements.write_bytes(b"# locked runtime dependencies\n")
    return tmp_path / "venvs", requirements, executable, calls


@pytest.mark.parametrize("changed", ["requirements", "python"])
def test_reuse_requires_the_same_lock_and_interpreter(preparation, changed):
    environments, requirements, executable, calls = preparation
    active = prepare_pi_venv.prepare_environment(environments, requirements)
    assert prepare_pi_venv.prepare_environment(environments, requirements) == active
    assert len(calls) == 2

    if changed == "requirements":
        requirements.write_bytes(b"# new lock")
    else:
        executable.write_bytes(b"new system Python build")

    prepared = prepare_pi_venv.prepare_environment(environments, requirements)
    assert prepared != active
    assert (active / "requirements.txt").read_bytes() == b"# locked runtime dependencies\n"
    assert len(calls) == 4


def test_failed_install_keeps_ready_environment_and_ignores_partial_build(preparation, monkeypatch):
    environments, requirements, _, _ = preparation
    active = prepare_pi_venv.prepare_environment(environments, requirements)
    requirements.write_bytes(b"# new lock")
    run = prepare_pi_venv.subprocess.run

    def fail_install(command, **kwargs):
        if command[2] == "pip":
            raise subprocess.CalledProcessError(1, command)
        return run(command, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(prepare_pi_venv.subprocess, "run", fail_install)
        with pytest.raises(subprocess.CalledProcessError):
            prepare_pi_venv.prepare_environment(environments, requirements)
    assert list(environments.iterdir()) == [active]

    prepared = prepare_pi_venv.prepare_environment(environments, requirements)
    (prepared / ".ruuvihub-environment").unlink()
    retry = prepare_pi_venv.prepare_environment(environments, requirements)
    assert retry not in {prepared, active}
    assert prepared.is_dir()
