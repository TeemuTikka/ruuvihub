"""Verify the Pi command against a disposable simulated installation."""

import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

if sys.platform != "linux":
    pytest.skip("managed Pi command requires Linux", allow_module_level=True)

import pwd
from pi_activation import ActivationError
import pi_update
import pi_update_data
from release_metadata import Artifact


@pytest.fixture
def managed(tmp_path, monkeypatch):
    base = tmp_path / "hub"
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "release-public.hex").write_text("aa" * 32)
    monkeypatch.setattr(pi_update, "_TOOLS", tools)
    monkeypatch.setattr(pi_update, "_require_trusted_path", lambda path: None)
    active = base / "releases/v1.1.0"
    (active / "src").mkdir(parents=True)
    (active / "src/main.py").touch()
    (active / "VERSION").write_text("v1.1.0\n")
    (base / "current").symlink_to(active)
    environment = base / "shared/venvs" / ("managed-" + "0" * 64 + "-12345678")
    (environment / "bin").mkdir(parents=True)
    (environment / "bin/python3").touch()
    (active / "venv").symlink_to(environment)
    calls = []

    def systemctl(command, *args, **kwargs):
        calls.append(command)
        if command == "show":
            python = base / "current/venv/bin/python3"
            return (
                f"User=nobody\nWorkingDirectory={base}/current\n"
                f"ExecStart={{ path={python} ; argv[]={python} {base}/current/src/main.py ; }}\n"
                f"Environment=SETTINGS_PATH={base}/shared/settings.local.json"
            )
        return "active" if command == "is-active" else ""

    def as_user(user, script, *args, **kwargs):
        assert user == "nobody"
        if script.name == "prepare_pi_venv.py":
            calls.append("prepare")
            return str(environment)
        calls.append(args[0])
        if args[0] == "backup":
            return "/private/backup"
        assert (base / "current").resolve().name == args[-1]

    def stage(artifact, parent):
        calls.append("stage")
        staging = parent / ".stage"
        (staging / "src").mkdir(parents=True)
        (staging / "src/main.py").touch()
        (staging / "requirements.txt").touch()
        (staging / "VERSION").write_text(artifact.version + "\n")
        return staging

    monkeypatch.setattr(pi_update, "_systemctl", systemctl)
    monkeypatch.setattr(pi_update, "_as_user", as_user)
    monkeypatch.setattr(pi_update, "stage_pi_release", stage)
    monkeypatch.setattr(pi_update, "latest_pi_artifact", lambda key: Artifact(
        "v1.2.0", "ruuvihub-v1.2.0.tar.gz", 1, "0" * 64,
    ))
    return base, calls


def test_update_prepares_before_stop_and_reverts_offline(managed, monkeypatch):
    base, calls = managed
    pi_update.manage(base, "update")
    assert calls == ["show", "stage", "prepare", "stop", "backup", "start", "is-active", "health"]
    assert (base / "current").resolve().name == "v1.2.0"
    monkeypatch.setattr(pi_update, "latest_pi_artifact", lambda key: pytest.fail("revert touched network"))
    (pi_update._TOOLS / "release-public.hex").unlink()
    calls.clear()
    pi_update.manage(base, "revert")
    assert calls == ["show", "stop", "start", "is-active", "health"]
    assert (base / "current").resolve().name == "v1.1.0"


def test_failed_backup_keeps_active_release_and_allows_retry(managed, monkeypatch):
    base, _ = managed
    original = pi_update._as_user

    def fail_backup(user, script, *args, **kwargs):
        if args[0] == "backup":
            raise subprocess.CalledProcessError(1, ["backup"])
        return original(user, script, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(pi_update, "_as_user", fail_backup)
        with pytest.raises(ActivationError, match="restored v1.1.0"):
            pi_update.manage(base, "update")
    assert (base / "current").resolve().name == "v1.1.0"
    assert not (base / "releases/v1.2.0").exists()
    pi_update.manage(base, "update")


def test_cli_refuses_legacy_unit(tmp_path, monkeypatch):
    monkeypatch.setattr(pi_update, "_systemctl", lambda *args: (
        f"User=nobody\nWorkingDirectory={tmp_path}/current\nExecStart=legacy shared/venv"
    ))
    with pytest.raises(ValueError, match="bootstrap it first"):
        pi_update._service_user(tmp_path)


def test_systemd_uses_original_settings_after_handoff(managed, monkeypatch):
    base, _ = managed
    settings = base / "shared/settings.local.json"
    original = base.parent / "legacy install/shared/settings.local.json"
    settings.symlink_to(original)
    systemctl = pi_update._systemctl
    with pytest.raises(ValueError, match="SETTINGS_PATH"):
        pi_update._service_user(base)
    monkeypatch.setattr(pi_update, "_systemctl", lambda *args: systemctl(*args).replace(
        f"SETTINGS_PATH={settings}", f'"SETTINGS_PATH={original}"',
    ))
    assert pi_update._service_user(base) == "nobody"


def test_update_obeys_lock_and_leaves_service_running_if_prepare_fails(managed, monkeypatch):
    base, calls = managed
    with pi_update._update_lock(base):
        with pytest.raises(ValueError, match="another managed Pi operation"):
            pi_update.manage(base, "update")
    assert calls == ["show"]
    calls.clear()
    monkeypatch.setattr(pi_update, "_as_user", lambda *args: "invalid-environment")
    with pytest.raises(ValueError, match="unexpected environment"):
        pi_update.manage(base, "update")
    assert calls == ["show", "stage"]
    assert list((base / "releases").iterdir()) == [base / "releases/v1.1.0"]


def test_update_replaces_an_unselected_release_left_by_interruption(managed):
    base, _ = managed
    orphan = base / "releases/v1.2.0"
    orphan.mkdir()
    (orphan / "interrupted").touch()
    pi_update.manage(base, "update")
    assert (base / "current").resolve() == orphan
    assert not (orphan / "interrupted").exists()
    assert len(list(orphan.parent.glob(".interrupted-*/v1.2.0/interrupted"))) == 1


def test_unhealthy_update_restarts_previous_release(managed, monkeypatch):
    base, calls = managed
    original = pi_update._as_user

    def unhealthy_target(user, script, *args, **kwargs):
        if args[0] == "health" and args[-1] == "v1.2.0":
            raise subprocess.CalledProcessError(1, ["health"])
        return original(user, script, *args, **kwargs)

    monkeypatch.setattr(pi_update, "_as_user", unhealthy_target)
    monkeypatch.setattr(pi_update.time, "sleep", lambda _: None)
    with pytest.raises(ActivationError, match="restored v1.1.0"):
        pi_update.manage(base, "update")
    assert (base / "current").resolve().name == "v1.1.0"
    assert calls[-4:] == ["stop", "start", "is-active", "health"]
    assert not (base / "releases/v1.2.0").exists()


def test_backup_includes_configured_database_and_does_not_replace_live_data(tmp_path):
    shared = tmp_path / "shared"
    (shared / "backups").mkdir(parents=True)
    custom_db = tmp_path / "custom readings.db"
    settings = json.dumps({"db_path": str(custom_db), "https_enabled": True})
    (shared / "settings.local.json").write_text(settings)
    with sqlite3.connect(custom_db) as database:
        database.execute("pragma journal_mode=wal")
        database.execute("create table readings (id int)")
        database.execute("insert into readings values (1)")
        database.commit()
        assert custom_db.with_name(custom_db.name + "-wal").stat().st_size > 32
        backup = pi_update_data.backup(tmp_path)
        database.execute("insert into readings values (2)")
    assert (backup / "settings.local.json").read_text() == settings
    assert backup.stat().st_mode & 0o077 == 0
    with sqlite3.connect(backup / "ruuvi_data.db") as snapshot:
        assert snapshot.execute("select id from readings").fetchall() == [(1,)]


@pytest.mark.skipif(
    os.geteuid() != 0 or shutil.which("runuser") is None or not Path("/usr/bin/python3").is_file(),
    reason="requires root, runuser and system Python in a disposable Linux environment",
)
def test_backup_helper_runs_under_isolated_system_python_as_service_user():
    account = pwd.getpwnam("nobody")
    with TemporaryDirectory() as directory:
        base = Path(directory)
        base.chmod(0o755)
        script = base / "pi_update_data.py"
        shutil.copyfile(pi_update_data.__file__, script)
        shared = base / "shared"
        backups = shared / "backups"
        backups.mkdir(parents=True)
        os.chown(backups, account.pw_uid, account.pw_gid)
        db = shared / "data.db"
        with sqlite3.connect(db) as connection:
            connection.execute("create table readings (id int)")
        (shared / "settings.local.json").write_text(json.dumps({"db_path": str(db)}))
        snapshot = Path(pi_update._as_user(account.pw_name, script, "backup", base))
        assert snapshot.parent == backups
        assert snapshot.stat().st_uid == account.pw_uid
        assert (snapshot / "ruuvi_data.db").is_file()
