# Coding agent instructions

This file gives repository-level guidance to coding agents that support
`AGENTS.md`, including Codex CLI.

- Read [README-dev.md](README-dev.md) for the newcomer workflow, architecture,
  development setup, and test commands. Use [README-windows.md](README-windows.md)
  for Windows-specific setup and [README-pi.md](README-pi.md) for deployment.
- Keep changes scoped to the requested task. Review and update the relevant
  README when behavior, settings, dependencies, or setup instructions change.
- Run relevant tests. The routine full suite is
  `python -m pytest tests/ -q --ignore=tests/test_connection.py`; that excluded
  test requires a Bluetooth adapter and a nearby RuuviTag.
- Do not include credentials, `settings.local.json`, local databases, or private
  sensor data in prompts, source changes, or commits.
- Follow the repository-specific documentation and shell-script guidance in
  [`.github/copilot-instructions.md`](.github/copilot-instructions.md).
