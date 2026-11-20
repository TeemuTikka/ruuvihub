---
description: "Use when adding or changing RuuviHub settings, configuration defaults, validation, persistence, or settings forms."
applyTo: "{config.json,src/config.py,src/settings.py,src/dashboard_preferences.py,src/web/config.py,src/web/dashboard_config.py,src/web/templates/config*.html,src/web/templates/dashboard_config.html,src/web/static/config.js,tests/test_config.py,tests/test_settings.py,tests/test_config_page.py,tests/test_dashboard_config.py}"
---

# Settings changes

- Trace the affected setting through bootstrap configuration, `Settings`,
  validation, persistence, consumers, and any editable form or API. Change
  only the surfaces that apply; not every setting is web-editable.
- Existing valid `settings.local.json` is the live source of truth.
  Bootstrap defaults do not overwrite it. Preserve operator values and
  corruption handling; never reset credentials as a migration fallback.
- Distinguish settings read dynamically from file-only or startup-only
  settings. Do not promise live reload for paths or other values whose
  consumers are initialized only at startup.
- For persisted install-time default changes, follow the migration timing
  and idempotence guidance in [copilot-instructions.md](../copilot-instructions.md).
  Cover an existing installation, the new default, and custom values.
- Test bootstrap and existing settings, invalid input, persistence and
  affected form behavior. For secrets, preserve masked inputs and ensure
  errors and logs do not disclose credentials. Review the relevant README.

See [README-dev.md](../../README-dev.md) for the settings architecture.
