---
description: "Use when changing FastAPI routes, admin authentication, CSRF, web forms, credentials, or security-sensitive frontend behavior."
applyTo: "{src/web/**/*.py,src/web/templates/**/*.html,src/web/static/**/*.js,src/web/static/*.js,tests/test_auth.py,tests/test_web_app.py,tests/test_config_page.py,tests/test_dashboard_config.py}"
---

# Web administration and security

- Preserve the intended public dashboard, history and CSV access versus
  authenticated administration. Hidden sensors are a display preference,
  not an access-control mechanism; see [SECURITY.md](../../SECURITY.md).
- Protect state-changing admin routes with the existing authentication and
  CSRF helpers in `src/web/auth.py`; apply the same checks to API and
  JavaScript-triggered actions. Keep cookie and HTTPS behavior consistent.
- Treat user-controlled input as untrusted. Preserve safe template escaping
  and DOM text rendering, validate destinations, and avoid exposing secrets
  in responses, redirects, client-side values, logs or exception messages.
- Test unauthenticated access, valid and missing/invalid CSRF tokens, success
  and validation failures as appropriate. For secret fields, test that
  leaving an input blank preserves the saved value without rendering it.
- Use temporary settings/database fixtures and synthetic values; never use
  real credentials or sensor data for tests or browser verification.
