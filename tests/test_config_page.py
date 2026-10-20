"""Tests for the config page and config AJAX API."""

import asyncio
import os
import sys
import time
from unittest.mock import AsyncMock, Mock, patch

import requests
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fastapi.testclient import TestClient

from mock_ruuvi import MAC_1, MAC_2
from settings import DEFAULT_UI_PASSWORD, DEFAULT_UI_USERNAME, SettingsStore
from storage import Storage
from web.app import create_app
from web.auth import compute_csrf_token


def build_app(tmp_path):
    storage = Storage(max_entries_per_tag=100, db_path=str(tmp_path / "t.db"))
    settings_store = SettingsStore(settings_path=str(tmp_path / "settings.local.json"))
    app = create_app(storage, settings_store)
    return app, settings_store, storage


def login(client: TestClient, username: str = DEFAULT_UI_USERNAME, password: str = DEFAULT_UI_PASSWORD):
    response = client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 303


def csrf_token(settings_store, username: str = DEFAULT_UI_USERNAME) -> str:
    settings = asyncio.run(settings_store.get())
    return compute_csrf_token(settings.session_secret, username)


def config_form_data(**overrides):
    data = {
        "scan_enabled": "on",
        "scan_interval_seconds": "60",
        "scan_timeout_seconds": "5",
        "upload_interval_seconds": "300",
        "max_entries_per_tag": "100",
        "tag_addresses": "",
        "db_retention_days": "0",
        "cloud_endpoint_url": "",
        "cloud_auth_header_name": "",
        "cloud_auth_header_value": "",
        "dashboard_refresh_seconds": "10",
        "dashboard_show_storage_info": "on",
        "history_backfill_interval_seconds": "3600",
        "history_backfill_failure_threshold": "5",
        "history_backfill_timeout_seconds": "120",
        "ui_username": DEFAULT_UI_USERNAME,
        "new_password": "",
        "confirm_new_password": "",
    }
    data.update(overrides)
    return data


def test_post_config_updates_history_backfill_settings(tmp_path):
    app, settings_store, _ = build_app(tmp_path)
    client = TestClient(app)
    login(client)
    token = csrf_token(settings_store)

    response = client.post(
        "/config",
        data=config_form_data(
            csrf_token=token,
            history_backfill_enabled="on",
            history_backfill_on_startup="on",
            history_backfill_periodic="on",
            history_backfill_interval_seconds="1800",
            history_backfill_failure_threshold="3",
            history_backfill_timeout_seconds="90",
        ),
        follow_redirects=False,
    )

    assert response.status_code == 303
    updated = asyncio.run(settings_store.get())
    assert updated.history_backfill_enabled is True
    assert updated.history_backfill_on_startup is True
    assert updated.history_backfill_periodic is True
    assert updated.history_backfill_interval_seconds == 1800
    assert updated.history_backfill_failure_threshold == 3
    assert updated.history_backfill_timeout_seconds == 90

    # Omitting the checkboxes on a subsequent save turns them back off.
    response = client.post(
        "/config",
        data=config_form_data(csrf_token=token),
        follow_redirects=False,
    )
    assert response.status_code == 303
    updated = asyncio.run(settings_store.get())
    assert updated.history_backfill_enabled is False
    assert updated.history_backfill_on_startup is False
    assert updated.history_backfill_periodic is False


def test_reenable_backfill_clears_disabled_tag(tmp_path):
    app, settings_store, _ = build_app(tmp_path)
    client = TestClient(app)
    asyncio.run(
        settings_store.update(
            history_backfill_disabled_tags={MAC_1: "5 consecutive backfill failures"}
        )
    )
    login(client)
    token = csrf_token(settings_store)

    response = client.post(
        "/config/reenable-backfill",
        data={"csrf_token": token, "mac": MAC_1},
        follow_redirects=False,
    )

    assert response.status_code == 303
    updated = asyncio.run(settings_store.get())
    assert updated.history_backfill_disabled_tags == {}


def test_config_page_shows_disabled_backfill_tags(tmp_path):
    app, settings_store, _ = build_app(tmp_path)
    client = TestClient(app)
    asyncio.run(
        settings_store.update(
            history_backfill_disabled_tags={MAC_1: "5 consecutive backfill failures"}
        )
    )
    login(client)

    response = client.get("/config")

    assert response.status_code == 200
    assert MAC_1 in response.text
    assert "5 consecutive backfill failures" in response.text


def test_config_page_redirects_to_login_when_unauthenticated(tmp_path):
    app, _, _ = build_app(tmp_path)
    client = TestClient(app)

    response = client.get("/config", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_unauthenticated_config_actions_cannot_change_settings_or_start_sync(tmp_path):
    app, settings_store, _ = build_app(tmp_path)
    client = TestClient(app)

    save = client.post(
        "/config",
        data=config_form_data(scan_interval_seconds="1"),
        follow_redirects=False,
    )
    reenable = client.post(
        "/config/reenable-backfill",
        data={"mac": MAC_1},
        follow_redirects=False,
    )
    sync = client.post("/config/sync-now", follow_redirects=False)
    upload_test = client.post("/api/config/test-upload", follow_redirects=False)

    assert save.status_code == 303
    assert save.headers["location"] == "/login"
    assert reenable.status_code == 303
    assert reenable.headers["location"] == "/login"
    assert sync.status_code == 303
    assert sync.headers["location"] == "/login"
    assert upload_test.status_code == 401
    assert asyncio.run(settings_store.get()).scan_interval_seconds == 60
    assert app.state.backfill_sync_status["running"] is False


def test_config_page_renders_current_settings_without_db_path_or_secret(tmp_path):
    app, settings_store, _ = build_app(tmp_path)
    client = TestClient(app)
    asyncio.run(
        settings_store.update(
            scan_interval_seconds=45,
            scan_timeout_seconds=8,
            upload_interval_seconds=123,
            tag_addresses=[MAC_1],
            tag_names={MAC_1: "Kitchen"},
            cloud_endpoint_url="https://example.com/ingest",
            cloud_auth_header_name="Authorization",
            cloud_auth_header_value="super-secret",
            dashboard_refresh_seconds=6,
        )
    )
    login(client)

    response = client.get("/config")

    assert response.status_code == 200
    assert 'value="45"' in response.text
    assert 'value="8"' in response.text
    assert 'value="123"' in response.text
    assert "Kitchen" in response.text
    assert "super-secret" not in response.text
    assert 'name="db_path"' not in response.text


def test_config_page_includes_tags_discovered_via_scanning_not_just_whitelist(tmp_path):
    """Tags seen while scanning in "scan all" mode (empty whitelist) must
    still show up with friendly-name controls, not just whitelisted MACs."""
    app, settings_store, storage = build_app(tmp_path)
    client = TestClient(app)
    asyncio.run(storage.add(MAC_1, {"mac": MAC_1, "timestamp": "2024-01-01T00:00:00+00:00"}))
    login(client)

    response = client.get("/config")

    assert response.status_code == 200
    assert MAC_1 in response.text
    assert "No known tags yet" not in response.text


def test_sync_now_triggers_background_backfill_and_updates_status(tmp_path):
    app, settings_store, _ = build_app(tmp_path)

    with TestClient(app) as client:
        login(client)
        token = csrf_token(settings_store)

        mock_backfill = AsyncMock(return_value=None)
        with patch("web.config.run_backfill_for_known_tags", new=mock_backfill):
            response = client.post(
                "/config/sync-now",
                data={"csrf_token": token},
                follow_redirects=False,
            )
            assert response.status_code == 303
            assert response.headers["location"] == "/config?syncing=1"

            # Background task runs on the same event loop TestClient keeps
            # alive inside this `with` block; give it a moment to finish.
            for _ in range(20):
                if not app.state.backfill_sync_status["running"]:
                    break
                time.sleep(0.05)

        mock_backfill.assert_awaited_once()
        assert app.state.backfill_sync_status["running"] is False
        assert app.state.backfill_sync_status["last_synced_at"] is not None


def test_sync_now_is_a_noop_when_already_running(tmp_path):
    app, settings_store, _ = build_app(tmp_path)

    with TestClient(app) as client:
        login(client)
        token = csrf_token(settings_store)
        app.state.backfill_sync_status["running"] = True

        mock_backfill = AsyncMock(return_value=None)
        with patch("web.config.run_backfill_for_known_tags", new=mock_backfill):
            response = client.post(
                "/config/sync-now",
                data={"csrf_token": token},
                follow_redirects=False,
            )
            assert response.status_code == 303

        mock_backfill.assert_not_awaited()


def test_post_config_requires_csrf_token(tmp_path):
    app, settings_store, _ = build_app(tmp_path)
    client = TestClient(app)
    login(client)

    response = client.post(
        "/config",
        data=config_form_data(scan_interval_seconds="45"),
        follow_redirects=False,
    )

    assert response.status_code == 403
    updated = asyncio.run(settings_store.get())
    assert updated.scan_interval_seconds == 60  # unchanged


def test_post_config_updates_settings_and_checkbox_can_toggle_off_and_on(tmp_path):
    app, settings_store, _ = build_app(tmp_path)
    client = TestClient(app)
    asyncio.run(
        settings_store.update(
            tag_addresses=[MAC_1, MAC_2],
            tag_names={MAC_1: "Old Name", MAC_2: "Remove Me"},
            dashboard_refresh_seconds=7,
            dashboard_show_storage_info=False,
        )
    )
    login(client)
    token = csrf_token(settings_store)

    response = client.post(
        "/config",
        data=config_form_data(
            csrf_token=token,
            scan_interval_seconds="30",
            scan_timeout_seconds="9",
            upload_interval_seconds="180",
            max_entries_per_tag="250",
            tag_addresses=f"{MAC_1}\n {MAC_2},",
            db_retention_days="14",
            cloud_endpoint_url="https://example.com/upload",
            cloud_auth_header_name="x-api-key",
            cloud_auth_header_value="fresh-secret",
            cloud_upload_enabled="on",
            tag_name_AA_BB_CC_DD_EE_01="Living Room",
            tag_name_AA_BB_CC_DD_EE_02="",
            dashboard_refresh_seconds="4",
        ),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/config?saved=1"

    updated = asyncio.run(settings_store.get())
    assert updated.scan_interval_seconds == 30
    assert updated.scan_timeout_seconds == 9
    assert updated.upload_interval_seconds == 180
    assert updated.max_entries_per_tag == 250
    assert updated.tag_addresses == [MAC_1, MAC_2]
    assert updated.db_retention_days == 14
    assert updated.cloud_endpoint_url == "https://example.com/upload"
    assert updated.cloud_auth_header_name == "x-api-key"
    assert updated.cloud_auth_header_value == "fresh-secret"
    assert updated.cloud_upload_enabled is True
    assert updated.scan_enabled is True
    assert updated.dashboard_refresh_seconds == 7
    assert updated.dashboard_show_storage_info is False
    assert updated.tag_names == {MAC_1: "Living Room"}

    disable_scan_payload = config_form_data(
        csrf_token=token,
        scan_interval_seconds="30",
        scan_timeout_seconds="9",
        upload_interval_seconds="180",
        max_entries_per_tag="250",
        tag_addresses=f"{MAC_1}\n{MAC_2}",
        db_retention_days="14",
        cloud_endpoint_url="https://example.com/upload",
        cloud_auth_header_name="x-api-key",
        cloud_auth_header_value="fresh-secret",
        tag_name_AA_BB_CC_DD_EE_01="Living Room",
        dashboard_refresh_seconds="4",
    )
    disable_scan_payload.pop("scan_enabled", None)
    disable_scan_payload.pop("dashboard_show_storage_info", None)

    client.post(
        "/config",
        data=disable_scan_payload,
        follow_redirects=False,
    )

    toggled = asyncio.run(settings_store.get())
    assert toggled.cloud_upload_enabled is False
    assert toggled.scan_enabled is False
    assert toggled.dashboard_show_storage_info is False
    assert toggled.dashboard_refresh_seconds == 7


def test_post_config_rejects_non_positive_intervals(tmp_path):
    app, settings_store, _ = build_app(tmp_path)
    client = TestClient(app)
    login(client)
    token = csrf_token(settings_store)

    response = client.post(
        "/config",
        data=config_form_data(csrf_token=token, scan_interval_seconds="0"),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/config?error=invalid_values"
    updated = asyncio.run(settings_store.get())
    assert updated.scan_interval_seconds == 60  # unchanged


def test_post_config_rejects_negative_retention(tmp_path):
    app, settings_store, _ = build_app(tmp_path)
    client = TestClient(app)
    login(client)
    token = csrf_token(settings_store)

    response = client.post(
        "/config",
        data=config_form_data(csrf_token=token, db_retention_days="-1"),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/config?error=invalid_values"


def test_post_config_rejects_enabling_upload_without_endpoint(tmp_path):
    app, settings_store, _ = build_app(tmp_path)
    client = TestClient(app)
    login(client)
    token = csrf_token(settings_store)

    response = client.post(
        "/config",
        data=config_form_data(csrf_token=token, cloud_upload_enabled="on", cloud_endpoint_url=""),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/config?error=missing_endpoint"
    updated = asyncio.run(settings_store.get())
    assert updated.cloud_upload_enabled is False


def test_post_config_password_change_updates_hash_and_rejects_old_password(tmp_path):
    app, settings_store, _ = build_app(tmp_path)
    client = TestClient(app)
    login(client)
    token = csrf_token(settings_store)

    response = client.post(
        "/config",
        data=config_form_data(csrf_token=token, new_password="new-secret", confirm_new_password="new-secret"),
        follow_redirects=False,
    )

    assert response.status_code == 303
    updated = asyncio.run(settings_store.get())
    assert updated.verify_login(DEFAULT_UI_USERNAME, "new-secret") is True
    assert updated.verify_login(DEFAULT_UI_USERNAME, DEFAULT_UI_PASSWORD) is False


def test_post_config_password_change_rotates_session_secret_and_invalidates_old_cookie(tmp_path):
    """A password reset must revoke previously-issued session cookies --
    otherwise a stolen 30-day cookie survives the reset."""
    app, settings_store, _ = build_app(tmp_path)
    old_client = TestClient(app)
    login(old_client)
    assert old_client.get("/config").status_code == 200

    new_client = TestClient(app)
    login(new_client)
    token = csrf_token(settings_store)
    response = new_client.post(
        "/config",
        data=config_form_data(csrf_token=token, new_password="new-secret", confirm_new_password="new-secret"),
        follow_redirects=False,
    )
    assert response.status_code == 303

    # The old session (issued before the password change/secret rotation)
    # must be logged out...
    assert old_client.get("/config", follow_redirects=False).status_code == 303
    # ...but the client performing the change gets a fresh valid cookie and
    # stays logged in.
    assert new_client.get("/config").status_code == 200


def test_post_config_blank_password_leaves_existing_hash_unchanged(tmp_path):
    app, settings_store, _ = build_app(tmp_path)
    client = TestClient(app)
    original_hash = asyncio.run(settings_store.get()).ui_password_hash
    login(client)
    token = csrf_token(settings_store)

    client.post("/config", data=config_form_data(csrf_token=token), follow_redirects=False)

    updated = asyncio.run(settings_store.get())
    assert updated.ui_password_hash == original_hash
    assert updated.verify_login(DEFAULT_UI_USERNAME, DEFAULT_UI_PASSWORD) is True


def test_post_config_password_mismatch_shows_error_and_keeps_password(tmp_path):
    app, settings_store, _ = build_app(tmp_path)
    client = TestClient(app)
    login(client)
    token = csrf_token(settings_store)

    response = client.post(
        "/config",
        data=config_form_data(csrf_token=token, new_password="one", confirm_new_password="two"),
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "do not match" in response.text
    updated = asyncio.run(settings_store.get())
    assert updated.verify_login(DEFAULT_UI_USERNAME, DEFAULT_UI_PASSWORD) is True


def test_post_config_blank_cloud_header_value_preserves_existing_secret(tmp_path):
    app, settings_store, _ = build_app(tmp_path)
    client = TestClient(app)
    asyncio.run(settings_store.update(cloud_auth_header_value="keep-me"))
    login(client)
    token = csrf_token(settings_store)

    client.post(
        "/config",
        data=config_form_data(
            csrf_token=token,
            cloud_endpoint_url="https://example.com/upload",
            cloud_auth_header_name="Authorization",
            cloud_auth_header_value="",
        ),
        follow_redirects=False,
    )

    updated = asyncio.run(settings_store.get())
    assert updated.cloud_auth_header_value == "keep-me"


def test_test_upload_api_requires_csrf_token(tmp_path):
    app, settings_store, _ = build_app(tmp_path)
    client = TestClient(app)
    login(client)

    with patch("uploader.requests.post") as post_mock:
        response = client.post(
            "/api/config/test-upload",
            data={"cloud_endpoint_url": "https://example.com/test"},
        )

    assert response.status_code == 403
    post_mock.assert_not_called()


def test_test_upload_api_returns_success_without_persisting_candidate_values(tmp_path):
    """The "Test connection" button must validate submitted values without
    ever saving them: if cloud upload were already enabled, persisting an
    unverified endpoint first would let the background loop immediately
    send real pending readings to it, and a failed test would clobber a
    previously-working configuration."""
    app, settings_store, _ = build_app(tmp_path)
    client = TestClient(app)
    asyncio.run(
        settings_store.update(
            cloud_endpoint_url="https://example.com/existing",
            cloud_auth_header_name="Authorization",
            cloud_auth_header_value="existing-secret",
        )
    )
    login(client)
    token = csrf_token(settings_store)
    mock_response = Mock(status_code=204)
    mock_response.raise_for_status.return_value = None

    with patch("uploader.requests.post", return_value=mock_response) as post_mock:
        response = client.post(
            "/api/config/test-upload",
            headers={"X-CSRF-Token": token},
            data={
                "cloud_endpoint_url": "https://example.com/test",
                "cloud_auth_header_name": "Authorization",
                "cloud_auth_header_value": "token-123",
            },
        )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert "204" in response.json()["message"]
    post_mock.assert_called_once()
    assert post_mock.call_args.args[0] == "https://example.com/test"
    assert post_mock.call_args.kwargs["headers"] == {"Authorization": "token-123"}

    # Candidate values were tested, not saved.
    updated = asyncio.run(settings_store.get())
    assert updated.cloud_endpoint_url == "https://example.com/existing"
    assert updated.cloud_auth_header_value == "existing-secret"
    assert updated.cloud_upload_enabled is False


def test_test_upload_api_returns_failure_json_without_touching_saved_settings(tmp_path):
    app, settings_store, _ = build_app(tmp_path)
    client = TestClient(app)
    asyncio.run(settings_store.update(cloud_auth_header_value="keep-me"))
    login(client)
    token = csrf_token(settings_store)

    with patch(
        "uploader.requests.post",
        side_effect=requests.RequestException("timed out"),
    ):
        response = client.post(
            "/api/config/test-upload",
            headers={"X-CSRF-Token": token},
            data={
                "cloud_endpoint_url": "https://example.com/test",
                "cloud_auth_header_name": "Authorization",
                "cloud_auth_header_value": "",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is False
    assert "timed out" in payload["message"]
    updated = asyncio.run(settings_store.get())
    assert updated.cloud_auth_header_value == "keep-me"


AZURE_CONTAINER = "https://example.blob.core.windows.net/data?sv=2026-02-06&sr=c&sp=cw&sig=fake%2Bsignature%3D"


def test_config_destination_persists_and_preserves_masked_azure_url(tmp_path):
    app, store, _ = build_app(tmp_path)
    client = TestClient(app)
    login(client)
    token = csrf_token(store)
    result = client.post("/config", data=config_form_data(csrf_token=token,
        cloud_upload_destination="azure_blob", azure_blob_container_url=AZURE_CONTAINER,
        cloud_upload_enabled="on"), follow_redirects=False)
    assert result.headers["location"] == "/config?saved=1"
    saved = asyncio.run(store.get())
    assert saved.cloud_upload_destination == "azure_blob"
    assert saved.azure_blob_container_url == AZURE_CONTAINER
    assert saved.cloud_upload_enabled
    page = client.get("/config").text
    assert '<option value="azure_blob" selected>' in page
    assert "HTTP endpoint" in page and "Azure Blob Storage" in page
    assert "fake" not in page and "sig=" not in page
    assert 'id="azure_blob_container_url" name="azure_blob_container_url" type="password" value=""' in page
    # An unrelated save with an empty password input must keep the SAS.
    result = client.post("/config", data=config_form_data(csrf_token=token,
        cloud_upload_destination="azure_blob", azure_blob_container_url="", cloud_upload_enabled="on"), follow_redirects=False)
    assert result.headers["location"] == "/config?saved=1"
    restarted = SettingsStore(settings_path=str(tmp_path / "settings.local.json"))
    assert restarted.get_sync().azure_blob_container_url == AZURE_CONTAINER
    assert restarted.get_sync().cloud_upload_destination == "azure_blob"


@pytest.mark.parametrize("destination,url,error", [("unknown", "", "invalid_destination"),
    ("azure_blob", "", "missing_azure_container"), ("azure_blob", "https://example.com/data", "invalid_azure_container")])
def test_config_rejects_invalid_upload_destinations(tmp_path, destination, url, error):
    app, store, _ = build_app(tmp_path)
    client = TestClient(app)
    login(client)
    response = client.post("/config", data=config_form_data(csrf_token=csrf_token(store),
        cloud_upload_destination=destination, azure_blob_container_url=url, cloud_upload_enabled="on"), follow_redirects=False)
    assert response.headers["location"] == f"/config?error={error}"
    assert asyncio.run(store.get()).cloud_upload_destination == "http"
    assert not asyncio.run(store.get()).cloud_upload_enabled


def test_test_connection_uses_candidate_destination_without_saving(tmp_path):
    app, store, _ = build_app(tmp_path)
    client = TestClient(app)
    login(client)
    response = requests.Response()
    response.status_code = 201
    with patch("uploader.requests.put", return_value=response) as put, patch("uploader.requests.post") as post:
        result = client.post("/api/config/test-upload", headers={"X-CSRF-Token": csrf_token(store)},
            data={"cloud_upload_destination":"azure_blob", "azure_blob_container_url":AZURE_CONTAINER})
    assert result.json()["success"]
    put.assert_called_once()
    post.assert_not_called()
    assert asyncio.run(store.get()).cloud_upload_destination == "http"
    assert asyncio.run(store.get()).azure_blob_container_url == ""


def test_test_connection_uses_saved_azure_url_when_input_blank(tmp_path):
    app, store, _ = build_app(tmp_path)
    asyncio.run(store.update(cloud_upload_destination="azure_blob", azure_blob_container_url=AZURE_CONTAINER))
    client = TestClient(app)
    login(client)
    response = requests.Response()
    response.status_code = 201
    with patch("uploader.requests.put", return_value=response) as put:
        result = client.post("/api/config/test-upload", headers={"X-CSRF-Token":csrf_token(store)},
                            data={"cloud_upload_destination":"azure_blob", "azure_blob_container_url":""})
    assert result.json()["success"]
    assert "fake%2Bsignature%3D" in put.call_args.args[0]
