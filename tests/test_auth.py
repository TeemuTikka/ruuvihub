"""Tests for src/web/auth.py – login/logout, session cookie, and the
escalating-delay throttle on failed login attempts.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fastapi import Depends
from fastapi.testclient import TestClient

from settings import DEFAULT_UI_PASSWORD, DEFAULT_UI_USERNAME, SettingsStore
from storage import Storage
from web.app import create_app
from web.auth import require_login_api, require_login_page


def build_app(tmp_path):
    storage = Storage(max_entries_per_tag=100, db_path=str(tmp_path / "t.db"))
    settings_store = SettingsStore(settings_path=str(tmp_path / "settings.local.json"))
    app = create_app(storage, settings_store)

    # Throwaway protected routes to exercise the auth dependencies –
    # dashboard/sensor/config routes will use the same dependencies once
    # they exist (added by later todos).
    @app.get("/__protected_page")
    async def _protected_page(username: str = Depends(require_login_page)):
        return {"username": username}

    @app.get("/__protected_api")
    async def _protected_api(username: str = Depends(require_login_api)):
        return {"username": username}

    return app, settings_store


def test_login_form_renders(tmp_path):
    app, _ = build_app(tmp_path)
    client = TestClient(app)

    response = client.get("/login")

    assert response.status_code == 200
    assert "Log in" in response.text


def test_login_with_correct_credentials_sets_cookie_and_redirects(tmp_path):
    app, _ = build_app(tmp_path)
    client = TestClient(app)

    response = client.post(
        "/login",
        data={"username": DEFAULT_UI_USERNAME, "password": DEFAULT_UI_PASSWORD},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "ruuvihub_session" in response.cookies


def test_login_cookie_is_secure_when_https_effectively_enabled(tmp_path):
    """The session cookie must be marked Secure whenever HTTPS is the
    effective transport, so it's never sent in cleartext to the plain-
    HTTP compatibility listener (old bookmarks)."""
    storage = Storage(max_entries_per_tag=100, db_path=str(tmp_path / "t.db"))
    settings_store = SettingsStore(settings_path=str(tmp_path / "settings.local.json"))
    app = create_app(storage, settings_store, https_enabled=True)
    client = TestClient(app)

    response = client.post(
        "/login",
        data={"username": DEFAULT_UI_USERNAME, "password": DEFAULT_UI_PASSWORD},
        follow_redirects=False,
    )

    set_cookie_header = response.headers["set-cookie"]
    assert "Secure" in set_cookie_header


def test_login_cookie_is_not_secure_in_plain_http_debug_mode(tmp_path):
    """The debug/no-HTTPS mode (https_enabled=False) must not mark the
    cookie Secure, or it would never be sent back over plain HTTP."""
    storage = Storage(max_entries_per_tag=100, db_path=str(tmp_path / "t.db"))
    settings_store = SettingsStore(settings_path=str(tmp_path / "settings.local.json"))
    app = create_app(storage, settings_store, https_enabled=False)
    client = TestClient(app)

    response = client.post(
        "/login",
        data={"username": DEFAULT_UI_USERNAME, "password": DEFAULT_UI_PASSWORD},
        follow_redirects=False,
    )

    set_cookie_header = response.headers["set-cookie"]
    assert "Secure" not in set_cookie_header


def test_login_with_wrong_password_shows_error_and_no_cookie(tmp_path):
    app, _ = build_app(tmp_path)
    client = TestClient(app)

    response = client.post(
        "/login",
        data={"username": DEFAULT_UI_USERNAME, "password": "wrong"},
    )

    assert response.status_code == 401
    assert "Invalid username or password" in response.text
    assert "ruuvihub_session" not in response.cookies


def test_protected_page_redirects_to_login_when_unauthenticated(tmp_path):
    app, _ = build_app(tmp_path)
    client = TestClient(app)

    response = client.get("/__protected_page", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_protected_api_returns_401_when_unauthenticated(tmp_path):
    app, _ = build_app(tmp_path)
    client = TestClient(app)

    response = client.get("/__protected_api")

    assert response.status_code == 401


def test_protected_routes_accessible_after_login(tmp_path):
    app, _ = build_app(tmp_path)
    client = TestClient(app)

    client.post(
        "/login",
        data={"username": DEFAULT_UI_USERNAME, "password": DEFAULT_UI_PASSWORD},
    )

    page_response = client.get("/__protected_page")
    api_response = client.get("/__protected_api")

    assert page_response.status_code == 200
    assert page_response.json() == {"username": DEFAULT_UI_USERNAME}
    assert api_response.status_code == 200


def test_logout_clears_session_and_reprotects_routes(tmp_path):
    app, _ = build_app(tmp_path)
    client = TestClient(app)
    client.post(
        "/login",
        data={"username": DEFAULT_UI_USERNAME, "password": DEFAULT_UI_PASSWORD},
    )
    assert client.get("/__protected_api").status_code == 200

    config_response = client.get("/config")
    csrf_match = re.search(r'name="csrf_token" value="([^"]*)"', config_response.text)
    token = csrf_match.group(1) if csrf_match else ""

    response = client.post("/logout", data={"csrf_token": token}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert client.get("/__protected_api").status_code == 401


def test_logout_requires_csrf_token(tmp_path):
    app, _ = build_app(tmp_path)
    client = TestClient(app)
    client.post(
        "/login",
        data={"username": DEFAULT_UI_USERNAME, "password": DEFAULT_UI_PASSWORD},
    )

    response = client.post("/logout", follow_redirects=False)

    assert response.status_code == 403
    assert client.get("/__protected_api").status_code == 200


def test_login_already_authenticated_redirects_home(tmp_path):
    app, _ = build_app(tmp_path)
    client = TestClient(app)
    client.post(
        "/login",
        data={"username": DEFAULT_UI_USERNAME, "password": DEFAULT_UI_PASSWORD},
    )

    response = client.get("/login", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_repeated_failures_trigger_escalating_throttle(tmp_path):
    """After a failed attempt, an immediate retry must be throttled (429)
    rather than re-checking credentials, per decision #10."""
    app, _ = build_app(tmp_path)
    client = TestClient(app)

    first = client.post(
        "/login", data={"username": DEFAULT_UI_USERNAME, "password": "wrong"}
    )
    assert first.status_code == 401

    second = client.post(
        "/login", data={"username": DEFAULT_UI_USERNAME, "password": "wrong"}
    )
    assert second.status_code == 429
    assert "Too many attempts" in second.text


def test_throttle_is_scoped_per_client_not_global(tmp_path):
    """A different client (source IP) must not be locked out by another
    client's failed login attempts -- the throttle is per-key, not a
    single app-wide bucket that any unauthenticated visitor could jam."""
    app, _ = build_app(tmp_path)
    attacker = TestClient(app, client=("10.0.0.1", 50000))
    victim = TestClient(app, client=("10.0.0.2", 50000))

    for _ in range(3):
        attacker.post("/login", data={"username": DEFAULT_UI_USERNAME, "password": "wrong"})

    # The attacker's own next attempt is throttled...
    attacker_retry = attacker.post(
        "/login", data={"username": DEFAULT_UI_USERNAME, "password": "wrong"}
    )
    assert attacker_retry.status_code == 429

    # ...but the victim, from a different source, can still log in.
    victim_login = victim.post(
        "/login",
        data={"username": DEFAULT_UI_USERNAME, "password": DEFAULT_UI_PASSWORD},
        follow_redirects=False,
    )
    assert victim_login.status_code == 303


def test_throttle_resets_after_successful_login():
    """Unit-level check: a successful login must zero out the throttle so
    the next failure starts a fresh 1s delay instead of continuing to
    escalate from before the reset."""
    from web.auth import LoginThrottle

    throttle = LoginThrottle()
    throttle.record_failure()
    throttle.record_failure()
    assert throttle.seconds_until_allowed() > 1  # escalated past the 1s baseline

    throttle.record_success()
    assert throttle.seconds_until_allowed() == 0

    throttle.record_failure()
    # Back to the 1s baseline, not continuing the earlier escalation.
    assert 0 < throttle.seconds_until_allowed() <= 1


def test_throttle_tracks_independent_keys():
    """Unit-level check: failures recorded under one key must not affect
    a different key's throttle state."""
    from web.auth import LoginThrottle

    throttle = LoginThrottle()
    throttle.record_failure("client-a")
    throttle.record_failure("client-a")

    assert throttle.seconds_until_allowed("client-a") > 1
    assert throttle.seconds_until_allowed("client-b") == 0


def test_username_rename_invalidates_existing_session(tmp_path):
    """Renaming the account (e.g. via the future config page) must log out
    any session issued under the old username."""
    app, settings_store = build_app(tmp_path)
    client = TestClient(app)
    client.post(
        "/login",
        data={"username": DEFAULT_UI_USERNAME, "password": DEFAULT_UI_PASSWORD},
    )
    assert client.get("/__protected_api").status_code == 200

    import asyncio

    asyncio.run(settings_store.update(ui_username="someone_else"))

    assert client.get("/__protected_api").status_code == 401
