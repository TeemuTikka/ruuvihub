"""Tests for src/web/app.py – the FastAPI app skeleton.

Full route coverage (auth, dashboard, sensor history, config) lands in
tests/test_web.py once those routes exist; this file only verifies the
app factory itself: shared state wiring, health check, and static
mounting, using FastAPI's TestClient (synchronous, no real network I/O
or uvicorn.Server needed).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fastapi.testclient import TestClient

from settings import SettingsStore
from storage import Storage
from web.app import create_app, create_redirect_app, run_web_server, _https_effectively_enabled


def build_app(tmp_path):
    storage = Storage(max_entries_per_tag=100, db_path=str(tmp_path / "t.db"))
    settings_store = SettingsStore(settings_path=str(tmp_path / "settings.local.json"))
    app = create_app(storage, settings_store)
    return app, storage, settings_store


def test_healthz_returns_ok(tmp_path):
    app, _, _ = build_app(tmp_path)
    client = TestClient(app)

    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    # Falls back to "dev" outside a release archive (no VERSION file).
    assert body["version"] == "dev"


def test_app_state_shares_storage_and_settings_store(tmp_path):
    """The app must use the exact Storage/SettingsStore instances passed in
    (not copies), so web routes see the same live data as scan_loop/
    upload_loop without any separate DB polling or IPC."""
    app, storage, settings_store = build_app(tmp_path)

    assert app.state.storage is storage
    assert app.state.settings_store is settings_store


def test_static_files_are_mounted(tmp_path):
    app, _, _ = build_app(tmp_path)
    client = TestClient(app)

    # No static assets exist yet (added by the frontend-assets todo), but
    # the mount itself must respond (404 for a missing file, not a routing
    # error), proving StaticFiles is wired up correctly.
    response = client.get("/static/does-not-exist.css")

    assert response.status_code == 404


def test_redirect_app_sends_https_308(tmp_path):
    redirect_app = create_redirect_app(https_port=8443)
    client = TestClient(redirect_app, base_url="http://example.local:8080")

    response = client.get("/sensor/AA:BB?range=24h", follow_redirects=False)

    assert response.status_code == 308
    assert response.headers["location"] == "https://example.local:8443/sensor/AA:BB?range=24h"


def test_redirect_app_serves_healthz_directly(tmp_path):
    """/healthz must not be redirected: the pre-HTTPS update.sh shipped
    with older releases only knows to check the plain-HTTP /healthz for
    version JSON, and a 308 response has no body for it to parse."""
    redirect_app = create_redirect_app(https_port=8443)
    client = TestClient(redirect_app, base_url="http://example.local:8080")

    response = client.get("/healthz", follow_redirects=False)

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_https_effectively_enabled_honors_setting_when_no_env_override(monkeypatch):
    monkeypatch.delenv("RUUVIHUB_DISABLE_HTTPS", raising=False)
    assert _https_effectively_enabled(True) is True
    assert _https_effectively_enabled(False) is False


def test_https_effectively_enabled_env_override_forces_http(monkeypatch):
    monkeypatch.setenv("RUUVIHUB_DISABLE_HTTPS", "true")
    assert _https_effectively_enabled(True) is False


@pytest.mark.asyncio
async def test_run_web_server_rejects_https_port_collision(tmp_path):
    """https_port colliding with the plain-HTTP port (while redirects are
    enabled) must fail fast with a clear error instead of one of the two
    uvicorn servers silently failing to bind."""
    storage = Storage(max_entries_per_tag=100, db_path=str(tmp_path / "t.db"))
    settings_store = SettingsStore(settings_path=str(tmp_path / "settings.local.json"))
    await settings_store.update(https_redirect_http=True, https_port=8080)

    with pytest.raises(RuntimeError, match="collides"):
        await run_web_server(storage, settings_store, port=8080)
