"""Exercise background release checks and the admin-only controls."""

import asyncio
from threading import Event

from fastapi.testclient import TestClient
import pytest
import requests

import release_check
from release_check import ReleaseCheck, fetch_latest_version
from settings import DEFAULT_UI_PASSWORD, DEFAULT_UI_USERNAME, SettingsStore
from storage import Storage
from web.app import create_app
from web.auth import compute_csrf_token


@pytest.mark.parametrize("status, release, expected", [
    (200, {"tag_name": "v1.10.0"}, "v1.10.0"),
    (200, {"tag_name": "v2.0.0", "draft": True}, None),
    (200, {"tag_name": "v2.0.0", "prerelease": True}, None),
    (404, {}, None),
])
def test_fetch_latest_release(monkeypatch, status, release, expected):
    class Response:
        status_code = status

        def json(self):
            return release

    def get(url, *, headers, timeout, allow_redirects):
        assert url == "https://api.github.com/repos/TeemuTikka/ruuvihub/releases/latest"
        assert timeout and not allow_redirects
        return Response()

    monkeypatch.setattr(requests, "get", get)
    assert fetch_latest_version() == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("current, available", [("v1.9.9", True), ("v1.10.0", False), ("dev", False)])
async def test_release_comparison_and_failure_preserve_last_known_release(monkeypatch, current, available):
    monkeypatch.setattr(release_check, "fetch_latest_version", lambda: "v1.10.0")
    check = ReleaseCheck(current)
    await check.start_check()
    assert check.update_available is available
    assert check.release_notes == "https://github.com/TeemuTikka/ruuvihub/releases/tag/v1.10.0"
    assert check.last_checked_at and not check.failed

    def unavailable():
        raise requests.Timeout()

    monkeypatch.setattr(release_check, "fetch_latest_version", unavailable)
    await check.start_check()
    assert check.failed and check.latest_version == "v1.10.0"


@pytest.mark.asyncio
async def test_release_check_runs_periodically(monkeypatch):
    fetched = asyncio.Queue()
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(release_check, "CHECK_INTERVAL_SECONDS", 0.01)

    def fetch():
        loop.call_soon_threadsafe(fetched.put_nowait, None)

    monkeypatch.setattr(release_check, "fetch_latest_version", fetch)
    check = ReleaseCheck("dev")
    periodic = asyncio.create_task(check.run_periodically())
    try:
        for _ in range(2):
            await asyncio.wait_for(fetched.get(), timeout=5)
    finally:
        periodic.cancel()
        await asyncio.gather(periodic, return_exceptions=True)
        await check.close()


def test_startup_check_is_nonblocking_and_admin_can_check_again(tmp_path, monkeypatch):
    started, release_request, second_check = Event(), Event(), Event()
    calls = []

    def fetch():
        calls.append(True)
        if len(calls) == 1:
            started.set()
            assert release_request.wait(10)
        else:
            second_check.set()
            raise requests.Timeout()
        return "v1.10.0"

    monkeypatch.setattr(release_check, "fetch_latest_version", fetch)
    store = SettingsStore(settings_path=str(tmp_path / "settings.local.json"))
    app = create_app(Storage(db_path=str(tmp_path / "test.db")), store)
    with TestClient(app) as client:
        try:
            assert started.wait(5)
            assert client.get("/healthz").status_code == 200
            assert "Release status" not in client.get("/").text
            for method in ("get", "post"):
                anonymous = getattr(client, method)("/config/releases", follow_redirects=False)
                assert anonymous.status_code == 303 and anonymous.headers["location"] == "/login"
            client.post("/login", data={"username": DEFAULT_UI_USERNAME, "password": DEFAULT_UI_PASSWORD})
            settings = store.get_sync()
            token = compute_csrf_token(settings.session_secret, settings.ui_username)
            for data in ({}, {"csrf_token": "invalid"}):
                assert client.post("/config/releases", data=data).status_code == 403
            assert "Checking GitHub" in client.get("/config/releases").text
            response = client.post("/config/releases", data={"csrf_token": token}, follow_redirects=False)
            assert response.status_code == 303 and response.headers["location"] == "/config/releases"
            assert len(calls) == 1
        finally:
            release_request.set()

        async def wait_for_running_check():
            await asyncio.wait_for(app.state.release_check._task, timeout=5)

        client.portal.call(wait_for_running_check)
        page = client.get("/config/releases").text
        assert "v1.10.0" in page and "Development checkouts are notification-only." in page
        assert "Release notes" in page
        client.post("/config/releases", data={"csrf_token": token}, follow_redirects=False)
        assert second_check.wait(5)
        client.portal.call(wait_for_running_check)
        assert "Showing the last known release." in client.get("/config/releases").text
