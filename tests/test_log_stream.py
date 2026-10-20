"""Tests for src/web/logstream.py – the LogStreamHandler ring buffer and
the /ws/logs live log stream route.
"""

import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from settings import DEFAULT_UI_PASSWORD, DEFAULT_UI_USERNAME, SettingsStore
from storage import Storage
from web.app import create_app
from web.logstream import LogStreamHandler


@pytest.fixture(autouse=True)
def _isolate_root_logger():
    """Snapshot/restore the root logger around every test in this file.

    create_app() attaches a LogStreamHandler to the *real* root logger,
    so without this, tests in this file would leak handlers/state into
    every other test file's logging (and vice versa) — the exact
    stacking bug this module's attach_to_root() is meant to prevent.
    """
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    yield
    for handler in list(root.handlers):
        if handler not in original_handlers:
            root.removeHandler(handler)
    root.handlers = original_handlers
    root.setLevel(original_level)


def build_client(tmp_path):
    storage = Storage(max_entries_per_tag=100, db_path=str(tmp_path / "t.db"))
    settings_store = SettingsStore(settings_path=str(tmp_path / "settings.local.json"))
    app = create_app(storage, settings_store)
    client = TestClient(app)
    client.post(
        "/login",
        data={"username": DEFAULT_UI_USERNAME, "password": DEFAULT_UI_PASSWORD},
    )
    return client, app


def test_buffer_evicts_oldest_beyond_capacity():
    handler = LogStreamHandler(capacity=3)
    logger = logging.getLogger("test.logstream.capacity")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        for i in range(5):
            logger.info("message %d", i)
    finally:
        logger.removeHandler(handler)

    snapshot = handler.snapshot()
    assert len(snapshot) == 3
    assert [entry["message"] for entry in snapshot] == ["message 2", "message 3", "message 4"]


def test_entry_shape_has_expected_fields():
    handler = LogStreamHandler()
    logger = logging.getLogger("test.logstream.shape")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        logger.warning("something happened")
    finally:
        logger.removeHandler(handler)

    entry = handler.snapshot()[0]
    assert entry["level"] == "WARNING"
    assert entry["levelno"] == logging.WARNING
    assert entry["logger"] == "test.logstream.shape"
    assert entry["message"] == "something happened"
    assert "ts" in entry
    assert entry["truncated"] is False


@pytest.mark.asyncio
async def test_subscriber_receives_broadcast_entry():
    handler = LogStreamHandler()
    queue = handler.subscribe()
    logger = logging.getLogger("test.logstream.subscriber")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        logger.error("boom")
        entry = await asyncio.wait_for(queue.get(), timeout=2)
    finally:
        logger.removeHandler(handler)
        handler.unsubscribe(queue)

    assert entry["level"] == "ERROR"
    assert entry["message"] == "boom"


def test_create_app_does_not_stack_handlers_on_repeated_calls(tmp_path):
    storage = Storage(max_entries_per_tag=100, db_path=str(tmp_path / "t.db"))
    settings_store = SettingsStore(settings_path=str(tmp_path / "settings.local.json"))

    create_app(storage, settings_store)
    create_app(storage, settings_store)
    create_app(storage, settings_store)

    root = logging.getLogger()
    stream_handlers = [h for h in root.handlers if isinstance(h, LogStreamHandler)]
    assert len(stream_handlers) == 1


def test_ws_logs_rejects_unauthenticated(tmp_path):
    storage = Storage(max_entries_per_tag=100, db_path=str(tmp_path / "t.db"))
    settings_store = SettingsStore(settings_path=str(tmp_path / "settings.local.json"))
    app = create_app(storage, settings_store)
    client = TestClient(app)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/logs"):
            pass
    assert exc_info.value.code == 1008


def test_ws_logs_sends_backlog_after_login(tmp_path):
    client, app = build_client(tmp_path)
    marker_logger = logging.getLogger("test.logstream.backlog")
    marker_logger.warning("backlog-marker-7f3a")

    # Sanity: the handler is really on root and captured the marker.
    snapshot_messages = [
        entry["message"] for entry in app.state.log_stream.snapshot()
    ]
    assert "backlog-marker-7f3a" in snapshot_messages

    with client.websocket_connect("/ws/logs") as ws:
        message = ws.receive_json()
        assert message["type"] == "backlog"
        matches = [
            entry
            for entry in message["entries"]
            if entry["message"] == "backlog-marker-7f3a"
        ]
        assert len(matches) == 1
        assert matches[0]["logger"] == "test.logstream.backlog"
        assert matches[0]["level"] == "WARNING"


def test_ws_logs_streams_live_records_after_backlog(tmp_path):
    client, app = build_client(tmp_path)
    logging.getLogger("test.logstream.live").warning("backlog-marker-7f3a")

    with client.websocket_connect("/ws/logs") as ws:
        backlog_message = ws.receive_json()
        assert backlog_message["type"] == "backlog"
        assert not any(
            entry["message"] == "live-marker-b91c"
            for entry in backlog_message["entries"]
        )

        logging.getLogger("test.logstream.live").warning("live-marker-b91c")

        found = None
        for _ in range(20):
            message = ws.receive_json()
            assert message["type"] == "log"
            entry = message["entry"]
            if entry["message"] == "live-marker-b91c":
                found = entry
                break
        assert found is not None, "live-marker-b91c not received within bound"
        assert found["logger"] == "test.logstream.live"
        assert found["level"] == "WARNING"


def test_static_logstream_js_is_served(tmp_path):
    client, _ = build_client(tmp_path)
    response = client.get("/static/logstream.js")
    assert response.status_code == 200
    assert "ruuvihub.logPanel.open" in response.text
    assert "/ws/logs" in response.text
    assert "MAX_RENDERED_LINES" in response.text
    assert "ruuvihub.logPanel.width" in response.text


@pytest.mark.asyncio
async def test_subscribe_with_snapshot_is_atomic():
    handler = LogStreamHandler()
    logger = logging.getLogger("test.logstream.atomic")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        logger.warning("old")
        backlog, queue = handler.subscribe_with_snapshot()
        assert [entry["message"] for entry in backlog] == ["old"]
        assert queue.empty()

        logger.warning("new")
        entry = await asyncio.wait_for(queue.get(), timeout=2)
        assert entry["message"] == "new"
    finally:
        logger.removeHandler(handler)
        handler.unsubscribe(queue)


@pytest.mark.asyncio
async def test_attach_to_root_detaches_previous_handler_subscribers():
    old = LogStreamHandler()
    old.attach_to_root()
    queue = old.subscribe()
    assert old.subscriber_count == 1

    new = LogStreamHandler()
    new.attach_to_root()

    root = logging.getLogger()
    assert old not in root.handlers
    assert old.subscriber_count == 0
    stream_handlers = [h for h in root.handlers if isinstance(h, LogStreamHandler)]
    assert stream_handlers == [new]

    new.detach()


def test_base_layout_includes_log_panel_when_logged_in(tmp_path):
    client, _ = build_client(tmp_path)
    response = client.get("/")
    assert response.status_code == 200
    assert 'id="log-panel-toggle"' in response.text
    assert 'id="log-panel"' in response.text
    assert 'id="log-panel-resize-handle"' in response.text
    assert "/static/logstream.js" in response.text


def test_login_page_has_no_log_panel(tmp_path):
    storage = Storage(max_entries_per_tag=100, db_path=str(tmp_path / "t.db"))
    settings_store = SettingsStore(settings_path=str(tmp_path / "settings.local.json"))
    app = create_app(storage, settings_store)
    client = TestClient(app)

    response = client.get("/login")

    assert response.status_code == 200
    assert 'id="log-panel"' not in response.text
    assert "/static/logstream.js" not in response.text
    assert "log-panel-open" not in response.text


def test_base_layout_preapplies_log_panel_open_class(tmp_path):
    client, _ = build_client(tmp_path)
    response = client.get("/")
    assert response.status_code == 200
    assert "log-panel-open" in response.text
    assert "ruuvihub.logPanel.open" in response.text

    # The pre-paint script must be parser-blocking (no defer/async) and must
    # appear before </head>, so it runs before the body (and the drawer) is
    # first painted -- this is the structural fix for the navigation flash.
    head, _, body = response.text.partition("</head>")
    script_start = head.index('<script>\n    // Applied before first paint')
    script_tag = head[script_start : script_start + len("<script>")]
    assert script_tag == "<script>"
    assert "log-panel-open" in head


def test_base_layout_inline_script_is_not_deferred(tmp_path):
    client, _ = build_client(tmp_path)
    response = client.get("/")
    head = response.text.partition("</head>")[0]
    assert '<script defer' not in head
    assert '<script async' not in head
    assert 'classList.add("log-panel-open")' in head
