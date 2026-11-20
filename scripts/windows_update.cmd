@echo off
setlocal DisableDelayedExpansion
set "VERSION="
for /f "delims=" %%V in ('%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -NonInteractive -Command "$id=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value; $task=Get-ScheduledTask -TaskPath '\' -TaskName ('RuuviHub-'+$id) -ErrorAction SilentlyContinue; if ($task) { $v=Split-Path -Leaf $task.Actions[0].WorkingDirectory; if ($v -cmatch '^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$') { $v } }"') do set "VERSION=%%V"
if not defined VERSION (
  echo No managed RuuviHub installation is selected. 1>&2
  exit /b 1
)
set "RELEASE=%~dp0releases\%VERSION%"
set "PYTHON=%RELEASE%\runtime\python-console.exe"
if not exist "%PYTHON%" set "PYTHON=%RELEASE%\runtime\python.exe"
"%PYTHON%" -I -B "%RELEASE%\update.py" --data-dir "%LOCALAPPDATA%\RuuviHub" %*
exit /b %ERRORLEVEL%
