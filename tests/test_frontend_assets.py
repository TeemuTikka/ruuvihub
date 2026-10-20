"""Tests for the frontend assets (base layout, CSS, vendored Chart.js, JS
helpers) added by the frontend-assets todo.

Page-specific templates (login/dashboard/sensor/config) are tested by
their own todos once those routes exist; this file only covers the
shared base.html layout and static asset serving.
"""

import gzip
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fastapi import Request
from fastapi.testclient import TestClient

from settings import DEFAULT_UI_PASSWORD, DEFAULT_UI_USERNAME, SettingsStore
from storage import Storage
from web.app import create_app


def build_client(tmp_path):
    storage = Storage(max_entries_per_tag=100, db_path=str(tmp_path / "t.db"))
    settings_store = SettingsStore(settings_path=str(tmp_path / "settings.local.json"))
    app = create_app(storage, settings_store)

    @app.get("/__test_render_base")
    async def _render_base(request: Request, using_default_credentials: bool = False):
        return app.state.templates.TemplateResponse(
            request,
            "base.html",
            {"using_default_credentials": using_default_credentials},
        )

    return TestClient(app)


def test_static_style_css_is_served(tmp_path):
    client = build_client(tmp_path)
    response = client.get("/static/style.css")
    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]


def test_static_app_js_is_served(tmp_path):
    client = build_client(tmp_path)
    response = client.get("/static/app.js")
    assert response.status_code == 200
    assert "startPolling" in response.text


def test_vendored_chartjs_is_served(tmp_path):
    """Chart.js must be vendored locally (no CDN dependency) so the UI
    works offline on a Pi with no internet access."""
    client = build_client(tmp_path)
    response = client.get("/static/chart.umd.min.js")
    assert response.status_code == 200
    assert "Chart.js" in response.text


def test_static_compression_preserves_asset_and_revalidation(tmp_path):
    client = build_client(tmp_path)
    plain = client.get("/static/chart.umd.min.js", headers={"Accept-Encoding": "identity"})
    wire_chunks = []
    app = client.app

    async def capture_app(scope, receive, send):
        async def capture_send(message):
            if message["type"] == "http.response.body":
                wire_chunks.append(message.get("body", b""))
            await send(message)
        await app(scope, receive, capture_send)

    # StaticFiles may stream, so Content-Length need not be present.
    client = TestClient(capture_app)
    compressed = client.get("/static/chart.umd.min.js", headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in plain.headers
    assert compressed.headers["content-encoding"] == "gzip"
    assert "accept-encoding" in compressed.headers["vary"].lower()
    # HTTPX decodes gzip; the browser must receive the identical local asset.
    assert compressed.content == plain.content
    assert wire_chunks, "The ASGI wrapper must capture the compressed response"
    wire_body = b"".join(wire_chunks)
    assert gzip.decompress(wire_body) == plain.content
    assert 0 < len(wire_body) < len(plain.content) // 2
    cached = client.get("/static/chart.umd.min.js", headers={
        "Accept-Encoding": "gzip", "If-None-Match": compressed.headers["etag"],
    })
    assert cached.status_code == 304
    assert cached.content == b""


def test_base_layout_renders_nav_and_hides_banner_by_default(tmp_path):
    client = build_client(tmp_path)
    response = client.get("/__test_render_base")
    assert response.status_code == 200
    assert "Dashboard" in response.text
    assert "Admin log in" in response.text
    assert 'class="settings-menu"' not in response.text
    assert "default admin credentials" not in response.text


def test_base_layout_shows_default_credentials_banner_when_flagged(tmp_path):
    client = build_client(tmp_path)
    response = client.get("/__test_render_base?using_default_credentials=true")
    assert response.status_code == 200
    assert "default admin credentials" not in response.text


def test_base_layout_shows_admin_controls_and_banner_after_login(tmp_path):
    client = build_client(tmp_path)
    response = client.post(
        "/login",
        data={"username": DEFAULT_UI_USERNAME, "password": DEFAULT_UI_PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 303

    response = client.get("/__test_render_base?using_default_credentials=true")
    assert response.status_code == 200
    assert 'class="settings-menu"' in response.text
    assert 'href="/config/dashboard"' in response.text
    assert 'href="/config"' in response.text
    assert "default admin credentials" in response.text
