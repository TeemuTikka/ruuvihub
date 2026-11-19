"""Exercise legacy migration without contacting GitHub or restarting a real service."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_pi_bootstrap import build_bootstrap


@pytest.mark.skipif(sys.platform != "linux", reason="the Pi bootstrap only runs on Linux")
def test_packaged_bootstrap_runs_in_isolated_python(tmp_path):
    bundle = tmp_path / "bootstrap.pyz"
    build_bootstrap(bundle, "01" * 32)
    help_text = subprocess.check_output([sys.executable, "-I", str(bundle), "--help"], text=True)
    assert "--legacy-base" in help_text


@pytest.mark.parametrize("key", ["invalid", "01" * 31])
def test_packaging_refuses_bad_key(tmp_path, key):
    bundle = tmp_path / "bootstrap.pyz"
    with pytest.raises(ValueError):
        build_bootstrap(bundle, key)
    assert not bundle.exists()


@pytest.mark.skipif(
    sys.platform != "linux" or getattr(os, "geteuid", lambda: -1)() != 0
    or shutil.which("runuser") is None or not Path("/usr/bin/python3").is_file(),
    reason="requires real Linux privilege separation in a disposable environment",
)
def test_bootstrap_publishes_same_app_and_environment_before_handoff(monkeypatch):
    from pi_activation import PiActivation
    import pi_bootstrap

    calls = []
    with TemporaryDirectory() as directory:
        parent = Path(directory)
        parent.chmod(0o755)
        legacy = parent / "legacy"
        release = legacy / "releases/v1.2.3"
        (release / "src").mkdir(parents=True)
        (release / "src/main.py").write_text("legacy deployed code")
        (release / "VERSION").write_text("v1.2.3\n")
        (release / "requirements.txt").write_text("legacy deployed lock")
        (legacy / "current").symlink_to(release)
        shared = legacy / "shared"
        environment = shared / "venvs/v1.2.3"
        (environment / "bin").mkdir(parents=True)
        (environment / "bin/python3").touch()
        (shared / "venv").symlink_to(environment)
        settings = shared / "settings.local.json"
        settings.write_text(json.dumps({
            "db_path": str(shared / "readings.db"),
            "log_path": str(shared / "logs/service.log"),
            "https_enabled": False,
        }))
        base = parent / "managed"
        python = shared / "venv/bin/python3"
        monkeypatch.setattr(pi_bootstrap, "_systemctl", lambda *args: (
            f"User=nobody\nWorkingDirectory={legacy}/current\n"
            f"ExecStart={{ path={python} ; argv[]={python} {legacy}/current/src/main.py ; }}\n"
            f"Environment=SETTINGS_PATH={settings} CONFIG_PATH={shared}/config.json"
        ))
        monkeypatch.setattr(pi_bootstrap, "_require_trusted_path", lambda *args: None)
        monkeypatch.setattr(pi_bootstrap, "_check_legacy_processes", lambda: calls.append("legacy check"))
        monkeypatch.setattr(pi_bootstrap, "latest_pi_artifact", lambda key: SimpleNamespace(version="v2.0.0"))

        def verify_handoff(path, user, version):
            assert (path, user, version) == (base, "nobody", "v1.2.3")
            activation = PiActivation(path, start=None, stop=None, healthy=None)
            assert activation.versions() == ("v1.2.3", None)
            assert (path / "current/src/main.py").read_text() == "legacy deployed code"
            assert (path / "current/venv").resolve() == environment
            assert (path / "shared/settings.local.json").resolve() == settings
            calls.append("handoff")

        monkeypatch.setattr(pi_bootstrap, "handoff", verify_handoff)
        scripts = Path(pi_bootstrap.__file__).parent
        payload = {name: (scripts / name).read_bytes() for name in pi_bootstrap._TOOLS if name.endswith(".py")}
        payload["release-public.hex"] = b"01" * 32
        pi_bootstrap.bootstrap(legacy, base, payload)

        assert calls == ["legacy check", "legacy check", "handoff"]
        assert (legacy / "current").resolve() == release


@pytest.mark.skipif(sys.platform != "linux", reason="the Pi installer only runs on Linux")
@pytest.mark.parametrize("healthy", [True, False])
def test_fresh_install_starts_only_after_verified_release_and_persistent_paths(tmp_path, monkeypatch, healthy):
    import pi_bootstrap

    base = tmp_path / "managed"
    units = tmp_path / "system"
    units.mkdir()
    unit = units / "ruuvihub.service"
    commands = []
    artifact = SimpleNamespace(version="v2.0.0")
    monkeypatch.setattr(pi_bootstrap, "_check_legacy_processes", lambda: None)
    monkeypatch.setattr(pi_bootstrap, "_require_trusted_path", lambda *args: None)
    monkeypatch.setattr(pi_bootstrap.os, "chown", lambda *args: None)
    monkeypatch.setattr(pi_bootstrap, "_service_user", lambda path: "nobody")
    monkeypatch.setattr(pi_bootstrap, "_healthy", lambda *args, **kwargs: healthy)
    monkeypatch.setattr(pi_bootstrap, "latest_pi_artifact", lambda key: artifact)

    def systemctl(command, *args):
        commands.append(command)
        if command == "show":
            return "not-found"
        if command == "start":
            config = json.loads((base / "shared/config.json").read_bytes())
            assert config["db_path"] == str(base / "shared/data/ruuvi_data.db")
            assert config["tls_key_path"] == str(base / "shared/data/certs/ruuvihub.key")
            assert (base / "current/src/main.py").is_file()
            assert f"ExecStart={base}/current/venv/bin/python3 {base}/current/src/main.py" in unit.read_text()

    def stage(verified, releases):
        assert verified is artifact
        staging = releases / ".staged"
        (staging / "src").mkdir(parents=True)
        (staging / "src/main.py").touch()
        (staging / "config.json").write_text("{}")
        return staging

    def prepare(path, user, staging, version, *, tools):
        assert (path, user, version, tools) == (base, "nobody", artifact.version, base / "tools")
        staging.rename(base / "releases" / version)

    monkeypatch.setattr(pi_bootstrap, "_systemctl", systemctl)
    monkeypatch.setattr(pi_bootstrap, "stage_pi_release", stage)
    monkeypatch.setattr(pi_bootstrap, "_prepare", prepare)
    payload = dict.fromkeys(pi_bootstrap._TOOLS, b"")
    payload["release-public.hex"] = b"01" * 32

    if healthy:
        pi_bootstrap.install(base, "nobody", payload, units)
        assert commands == ["show", "daemon-reload", "start", "enable"]
        assert unit.exists()
    else:
        with pytest.raises(RuntimeError, match="did not become healthy"):
            pi_bootstrap.install(base, "nobody", payload, units)
        assert commands == ["show", "daemon-reload", "start", "stop", "disable", "daemon-reload"]
        assert not unit.exists()
    assert (base / "shared/settings.local.json").readlink() == base / "shared/data/settings.local.json"
