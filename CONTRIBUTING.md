# Contributing to RuuviHub

Thanks for your interest in improving RuuviHub. Small, focused changes
with clear tests and documentation are easiest to review.

## Before you start

- For security vulnerabilities, follow [SECURITY.md](SECURITY.md) and report
  privately rather than opening a public issue or pull request.
- Check the existing GitHub issues and pull requests for related work.
- For a substantial change, open an issue first to agree on the intended
  behavior and scope.
- Read [README-dev.md](README-dev.md) for the newcomer workflow,
  architecture, and development guidance. Windows contributors can also
  use [README-windows.md](README-windows.md).

## Set up and run checks

Use Python 3.11, matching the project CI configuration. From the repository
root, create a virtual environment and install runtime and test dependencies:

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux:        source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install -r tests/requirements.txt
```

Run the hardware-free tests before opening a pull request:

```bash
python -m pytest tests/ -q --ignore=tests/test_connection.py
```

`tests/test_connection.py` requires a Bluetooth adapter and a RuuviTag in
range. Most other tests use mocks and need no BLE hardware. Run tests related
to your change while working, then run the full hardware-free suite.

## Pull requests

- Keep each pull request focused and explain the user-visible behavior it
  changes.
- Add or update tests for behavior changes.
- Update the relevant README when behavior, settings, dependencies, or setup
  instructions change.
- Include the test command and result in the pull request description. Note
  any checks you could not run and why.
- Do not include credentials, `settings.local.json`, databases, certificates,
  logs, or private sensor readings in commits or screenshots.
- Do not commit generated files such as virtual environments, bytecode, or
  local test data.
