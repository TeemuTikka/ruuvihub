"""Tests for src/uploader.py."""

import json
import os
import sys
from unittest.mock import Mock, patch

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from settings import SettingsStore
from storage import Storage
from uploader import Uploader
from mock_ruuvi import MAC_1, make_record


def write_config(tmp_path, data: dict) -> str:
    path = os.path.join(tmp_path, "config.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return path


def make_settings_store(tmp_path) -> SettingsStore:
    config_path = write_config(tmp_path, {})
    settings_path = str(tmp_path / "settings.local.json")
    return SettingsStore(settings_path=settings_path, config_path=config_path)


@pytest.mark.asyncio
async def test_upload_returns_true_for_empty_list(tmp_path):
    uploader = Uploader(make_settings_store(tmp_path))
    result = await uploader.upload([])
    assert result is True


@pytest.mark.asyncio
async def test_upload_logs_stub_when_endpoint_not_configured(tmp_path, caplog):
    uploader = Uploader(make_settings_store(tmp_path))
    record = make_record(mac=MAC_1)
    caplog.set_level("INFO")

    with patch("uploader.requests.post") as post_mock:
        result = await uploader.upload([record])

    assert result is True
    assert "UPLOAD STUB" in caplog.text
    post_mock.assert_not_called()


@pytest.mark.asyncio
async def test_upload_posts_records_with_auth_header(tmp_path):
    settings_store = make_settings_store(tmp_path)
    await settings_store.update(
        cloud_endpoint_url="https://example.com/ingest",
        cloud_auth_header_name="x-api-key",
        cloud_auth_header_value="secret",
    )
    uploader = Uploader(settings_store)
    response = Mock(status_code=200)
    response.raise_for_status.return_value = None
    records = [make_record(mac=MAC_1)]

    with patch("uploader.requests.post", return_value=response) as post_mock:
        result = await uploader.upload(records)

    assert result is True
    post_mock.assert_called_once_with(
        "https://example.com/ingest",
        json=records,
        headers={"x-api-key": "secret"},
        timeout=10,
    )
    response.raise_for_status.assert_called_once_with()


@pytest.mark.asyncio
async def test_upload_omits_auth_header_when_name_blank(tmp_path):
    settings_store = make_settings_store(tmp_path)
    await settings_store.update(
        cloud_endpoint_url="https://example.com/ingest",
        cloud_auth_header_name="",
        cloud_auth_header_value="ignored",
    )
    uploader = Uploader(settings_store)
    response = Mock(status_code=204)
    response.raise_for_status.return_value = None

    with patch("uploader.requests.post", return_value=response) as post_mock:
        result = await uploader.upload([make_record(mac=MAC_1)])

    assert result is True
    assert post_mock.call_args.kwargs["headers"] == {}


@pytest.mark.asyncio
async def test_upload_returns_false_when_enabled_but_endpoint_missing(tmp_path):
    """Enabling cloud upload without configuring an endpoint must not be
    reported as a successful upload -- doing so would silently drop the
    pending batch (storage.clear_pending) without ever sending it."""
    settings_store = make_settings_store(tmp_path)
    await settings_store.update(cloud_upload_enabled=True, cloud_endpoint_url="")
    uploader = Uploader(settings_store)

    with patch("uploader.requests.post") as post_mock:
        result = await uploader.upload([make_record(mac=MAC_1)])

    assert result is False
    post_mock.assert_not_called()


@pytest.mark.asyncio
async def test_upload_returns_false_on_request_exception(tmp_path):
    settings_store = make_settings_store(tmp_path)
    await settings_store.update(cloud_endpoint_url="https://example.com/ingest")
    uploader = Uploader(settings_store)

    with patch(
        "uploader.requests.post",
        side_effect=requests.RequestException("connection failed"),
    ):
        result = await uploader.upload([make_record(mac=MAC_1)])

    assert result is False


@pytest.mark.asyncio
async def test_test_upload_returns_error_when_endpoint_missing(tmp_path):
    uploader = Uploader(make_settings_store(tmp_path))

    with patch("uploader.requests.post") as post_mock:
        success, message = await uploader.test_upload()

    assert success is False
    assert message == "No cloud endpoint configured"
    post_mock.assert_not_called()


@pytest.mark.asyncio
async def test_test_upload_posts_synthetic_record_and_leaves_pending_queue_untouched(
    tmp_path,
):
    settings_store = make_settings_store(tmp_path)
    await settings_store.update(
        cloud_endpoint_url="https://example.com/test",
        cloud_auth_header_name="Authorization",
        cloud_auth_header_value="Bearer token",
    )
    uploader = Uploader(settings_store)
    storage = Storage(max_entries_per_tag=100, db_path=str(tmp_path / "test.db"))
    await storage.add(MAC_1, make_record(mac=MAC_1))
    pending_before = await storage.get_pending()

    response = Mock(status_code=201)
    response.raise_for_status.return_value = None

    with patch("uploader.requests.post", return_value=response) as post_mock:
        success, message = await uploader.test_upload()

    assert success is True
    assert "201" in message
    post_mock.assert_called_once()
    assert post_mock.call_args.args[0] == "https://example.com/test"
    assert post_mock.call_args.kwargs["headers"] == {
        "Authorization": "Bearer token"
    }
    payload = post_mock.call_args.kwargs["json"]
    assert len(payload) == 1
    assert payload[0]["mac"] == "TEST"
    assert payload[0]["test"] is True
    assert await storage.get_pending() == pending_before


@pytest.mark.asyncio
async def test_test_upload_returns_request_error_message(tmp_path):
    settings_store = make_settings_store(tmp_path)
    await settings_store.update(cloud_endpoint_url="https://example.com/test")
    uploader = Uploader(settings_store)

    with patch(
        "uploader.requests.post",
        side_effect=requests.RequestException("timed out"),
    ):
        success, message = await uploader.test_upload()

    assert success is False
    assert "timed out" in message


AZURE_CONTAINER = "https://example.blob.core.windows.net/data?sv=2026-02-06&sr=c&sp=cw&sig=fake%2Bsignature%3D"


@pytest.mark.asyncio
async def test_azure_upload_puts_unique_json_blobs_without_http_credentials(tmp_path):
    from urllib.parse import urlsplit
    store = make_settings_store(tmp_path)
    await store.update(cloud_upload_destination="azure_blob", azure_blob_container_url=AZURE_CONTAINER,
                       cloud_endpoint_url="https://example.com/old", cloud_auth_header_name="Authorization",
                       cloud_auth_header_value="http-secret")
    response = requests.Response()
    response.status_code = 201
    uploader = Uploader(store)
    records = [make_record()]
    with patch("uploader.requests.put", return_value=response) as put, patch("uploader.requests.post") as post:
        assert await uploader.upload(records)
        assert await uploader.upload(records)
    post.assert_not_called()
    urls = [call.args[0] for call in put.call_args_list]
    assert urls[0] != urls[1]
    for call in put.call_args_list:
        assert urlsplit(call.args[0]).query == urlsplit(AZURE_CONTAINER).query
        assert urlsplit(call.args[0]).path.startswith("/data/ruuvihub-")
        assert urlsplit(call.args[0]).path.endswith(".json")
        assert call.kwargs["json"] == records
        assert call.kwargs["headers"]["x-ms-blob-type"] == "BlockBlob"
        assert call.kwargs["headers"]["Content-Type"] == "application/json"
        assert call.kwargs["headers"]["If-None-Match"] == "*"
        assert "Authorization" not in call.kwargs["headers"]
        assert call.kwargs["allow_redirects"] is False


@pytest.mark.asyncio
async def test_azure_connection_override_does_not_change_settings_or_pending(tmp_path):
    store = make_settings_store(tmp_path)
    storage = Storage(db_path=str(tmp_path / "pending.db"))
    await storage.add(MAC_1, make_record())
    before = await storage.get_pending()
    response = requests.Response()
    response.status_code = 201
    with patch("uploader.requests.put", return_value=response) as put:
        success, message = await Uploader(store).test_upload(destination="azure_blob", azure_blob_container_url=AZURE_CONTAINER)
    assert success and "test JSON blob created" in message
    assert "/ruuvihub-test-" in put.call_args.args[0]
    assert put.call_args.kwargs["json"][0]["test"] is True
    assert (await store.get()).cloud_upload_destination == "http"
    assert (await store.get()).azure_blob_container_url == ""
    assert await storage.get_pending() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 403, 500])
async def test_azure_rejections_do_not_report_upload_success_or_leak_sas(tmp_path, caplog, status):
    store = make_settings_store(tmp_path)
    await store.update(cloud_upload_destination="azure_blob", azure_blob_container_url=AZURE_CONTAINER)
    response = requests.Response()
    response.status_code = status
    response.url = AZURE_CONTAINER
    response.headers["x-ms-error-code"] = "AuthorizationPermissionMismatch" if status == 403 else "TestError"
    with patch("uploader.requests.put", return_value=response):
        assert await Uploader(store).upload([make_record()]) is False
        success, message = await Uploader(store).test_upload()
    assert success is False
    assert str(status) in message
    assert "sig=" not in message and "signature" not in message
    assert "sig=" not in caplog.text and "signature" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("relative", [False, True])
async def test_azure_network_error_redacts_signed_url(tmp_path, caplog, relative):
    store = make_settings_store(tmp_path)
    await store.update(cloud_upload_destination="azure_blob", azure_blob_container_url=AZURE_CONTAINER)
    reported_url = AZURE_CONTAINER.split(".net", 1)[1] if relative else AZURE_CONTAINER
    with patch("uploader.requests.put", side_effect=requests.ConnectionError(f"Connection failed: {reported_url}")):
        assert await Uploader(store).upload([make_record()]) is False
        success, message = await Uploader(store).test_upload()
    assert not success
    assert "Connection failed" in message
    assert "fake%2Bsignature%3D" not in message + caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["", "http://example.blob.core.windows.net/data?sr=c&sig=x",
    "https://example.blob.core.windows.net/data", "https://example.blob.core.windows.net/data/blob?sr=c&sig=x",
    "https://example.blob.core.windows.net/data?sr=b&sig=x"])
async def test_azure_invalid_container_rejected_without_network(tmp_path, url):
    store = make_settings_store(tmp_path)
    with patch("uploader.requests.put") as put:
        success, message = await Uploader(store).test_upload(destination="azure_blob", azure_blob_container_url=url)
    assert not success and "container SAS URL" in message
    put.assert_not_called()
