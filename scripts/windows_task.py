"""Use one per-user Windows Task Scheduler action to select a release."""

import ctypes
import http.client
import json
import os
from pathlib import Path
import ssl
import subprocess
import time

from release_metadata import STABLE_VERSION

_TASK_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$name = 'RuuviHub-' + $sid
$task = @(Get-ScheduledTask -TaskPath '\' | Where-Object TaskName -CEQ $name)
if ($task.Count -gt 1) { throw 'Ambiguous RuuviHub task' }
switch ($env:RUUVI_TASK_OPERATION) {
    'read' {
        if ($task.Count) {
            $actions = @($task[0].Actions)
            if ($actions.Count -ne 1) { throw 'Expected one RuuviHub task action' }
            $actions[0] | Select-Object Execute, Arguments, WorkingDirectory | ConvertTo-Json -Compress
        }
    }
    'register' {
        if ($task.Count) { throw 'RuuviHub task already exists' }
        $action = New-ScheduledTaskAction -Execute $env:RUUVI_TASK_EXE `
            -Argument $env:RUUVI_TASK_ARGS -WorkingDirectory $env:RUUVI_TASK_DIR
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $sid
        $principal = New-ScheduledTaskPrincipal -UserId $sid -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
            -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
        Register-ScheduledTask -TaskName $name -TaskPath '\' -Action $action `
            -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
    }
    'select' {
        $action = New-ScheduledTaskAction -Execute $env:RUUVI_TASK_EXE `
            -Argument $env:RUUVI_TASK_ARGS -WorkingDirectory $env:RUUVI_TASK_DIR
        Set-ScheduledTask -TaskName $name -TaskPath '\' -Action $action | Out-Null
    }
    'start' { $task[0] | Start-ScheduledTask }
    'running' { if ($task[0].State -eq 'Running') { 'yes' } }
    'stop' {
        if ($task[0].State -eq 'Running' -or $task[0].State -eq 'Queued') {
            $task[0] | Stop-ScheduledTask
        }
        for ($i = 0; $i -lt 50; $i++) {
            $state = (Get-ScheduledTask -TaskName $name -TaskPath '\').State
            if ($state -ne 'Running' -and $state -ne 'Queued') { exit 0 }
            Start-Sleep -Milliseconds 200
        }
        throw 'RuuviHub task did not stop'
    }
    'remove' {
        if ($task.Count) { $task[0] | Unregister-ScheduledTask -Confirm:$false }
    }
}
"""


def _action(release: Path, data: Path) -> dict[str, str]:
    return {
        "Execute": str(release / "runtime/python.exe"),
        "Arguments": f'-I -B "{release / "run.py"}" --data-dir "{data}"',
        "WorkingDirectory": str(release),
    }


class UserTask:
    def __init__(self, base: Path, data: Path):
        self.base, self.data = base.resolve(), data.resolve()

    def _invoke(self, operation: str, action: dict[str, str] | None = None) -> str:
        env = os.environ | {"RUUVI_TASK_OPERATION": operation}
        if action is not None:
            env.update(zip(
                ("RUUVI_TASK_EXE", "RUUVI_TASK_ARGS", "RUUVI_TASK_DIR"),
                (action[key] for key in ("Execute", "Arguments", "WorkingDirectory")),
            ))
        powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        result = subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-Command", _TASK_SCRIPT],
            env=env, encoding="utf-8", errors="replace", capture_output=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or f"Task Scheduler {operation} failed")
        return result.stdout.strip()

    def release(self, version: str) -> Path:
        if not isinstance(version, str) or len(version) > 64 or not STABLE_VERSION.fullmatch(version):
            raise ValueError("invalid Windows release version")
        release = self.base / "releases" / version
        if release.resolve() != release or not all(
            (release / file).is_file() for file in ("runtime/python.exe", "src/main.py", "run.py", "VERSION")
        ) or (release / "VERSION").read_text(encoding="ascii").strip() != version:
            raise ValueError(f"release {version} is not prepared")
        return release

    def selected(self) -> str | None:
        output = self._invoke("read")
        if not output:
            return None
        action = json.loads(output)
        version = Path(action["WorkingDirectory"]).name
        if action != _action(self.release(version), self.data):
            raise ValueError("the scheduled task does not match the managed installation")
        return version

    def register(self, version: str) -> None:
        self._invoke("register", _action(self.release(version), self.data))

    def select(self, version: str) -> None:
        self._invoke("select", _action(self.release(version), self.data))

    def start(self) -> None:
        self._invoke("start")

    def stop(self) -> None:
        self._invoke("stop")

    def remove(self) -> None:
        self._invoke("remove")

    def healthy(self, version: str, *, timeout: float = 30) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                settings = json.loads((self.data / "settings.local.json").read_bytes())
                https = settings.get("https_enabled", True)
                port = settings.get("https_port", 8443) if https else 8080
                if type(port) is not int or not 1 <= port <= 65535:
                    return False
                if https:
                    # The app normally uses a self-signed certificate. Probe only loopback.
                    connection = http.client.HTTPSConnection(
                        "127.0.0.1", port, timeout=2, context=ssl._create_unverified_context(),
                    )
                else:
                    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                try:
                    connection.request("GET", "/healthz")
                    response = connection.getresponse()
                    if response.status == 200 and json.loads(response.read(4096)) == {
                        "status": "ok", "version": version,
                    }:
                        return self._invoke("running") == "yes"
                finally:
                    connection.close()
            except (OSError, ValueError, http.client.HTTPException):
                pass
            time.sleep(0.5)
        return False


def replace_durably(source: Path, destination: Path) -> None:
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        move = kernel.MoveFileExW
        move.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
        move.restype = ctypes.c_int
        if not move(str(source), str(destination), 0x1 | 0x8):  # REPLACE_EXISTING | WRITE_THROUGH
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        os.replace(source, destination)
