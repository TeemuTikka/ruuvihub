---
name: dashboard-validation
description: "Use when validating RuuviHub dashboard, sensor pages, frontend interactions, visual layout, accessibility, or UI performance changes."
---

# Dashboard validation workflow

1. Identify affected routes, templates, scripts and tests. Read the relevant
   dashboard and testing sections of `README-dev.md`; keep public and admin
   views and offline static assets consistent.
2. Run focused existing pytest cases for the affected page and frontend
   assets. Use synthetic records and temporary databases; never use real
   sensor data, credentials or a production service.
3. When visual or interaction verification is useful, run
   `python scripts/run_demo.py` in a temporary local process and open its
   localhost URL. The demo creates disposable settings and synthetic history;
   verify it is ready, then stop it when the check is complete. Exercise
   relevant desktop/mobile layouts, keyboard/focus behavior, stale and empty
   states, and admin controls as applicable. Do not claim browser validation
   if no browser is available.
4. For performance-sensitive work, follow the benchmark commands in
   `README-dev.md`, comparing equivalent inputs and conditions. Browser
   benchmarking uses optional Playwright tooling; do not install it unless
   necessary or already present. Synthetic timings alone do not prove Pi or
   real-browser performance.
5. Report the tests and visual/performance checks actually performed, and any
   untested browsers or hardware. Update affected user/developer documentation
   when behavior or setup changes.
