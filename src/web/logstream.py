"""Live log stream: a ring-buffer logging.Handler plus a /ws/logs route.

Attaches a small ``logging.Handler`` to the root logger that keeps the
last ``LOG_BUFFER_SIZE`` records in memory and fans out new ones to any
connected WebSocket subscriber (the log panel in base.html, decision:
see README-dev.md). This gives operators live visibility into what the
scan/upload loops are doing without SSH-ing into the Pi to tail a log
file.

The root logger's level is set to INFO by main.py's ``basicConfig`` (see
``main.py``); this module deliberately does not touch that. DEBUG-level
records are only fanned out through this handler if something else
lowers the root logger's level, since ``Handler.emit`` is only invoked
for records the logger itself decided to pass through.
"""

import asyncio
import logging
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from web.auth import require_login_ws

LOG_BUFFER_SIZE = 200
SUBSCRIBER_QUEUE_SIZE = 500
MAX_ENTRY_MESSAGE_CHARS = 2000

logger = logging.getLogger(__name__)


def _record_to_entry(record: logging.LogRecord) -> dict:
    """Convert a stdlib LogRecord into the small JSON-friendly dict sent
    to the browser and kept in the ring buffer."""
    message = record.getMessage()
    if record.exc_info:
        message = f"{message}\n{logging.Formatter().formatException(record.exc_info)}"
    truncated = len(message) > MAX_ENTRY_MESSAGE_CHARS
    if truncated:
        message = message[:MAX_ENTRY_MESSAGE_CHARS] + "…"
    return {
        "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
        "level": record.levelname,
        "levelno": record.levelno,
        "logger": record.name,
        "message": message,
        "truncated": truncated,
    }


class LogStreamHandler(logging.Handler):
    """Ring-buffer + fan-out logging.Handler backing the live log panel.

    Keeps the last ``capacity`` records in a deque (for the "backlog"
    sent to a newly-connected client) and pushes every new record onto
    each subscriber's asyncio.Queue. Logging can happen from any thread
    (scan/upload loops, uvicorn's worker threads), so the buffer is
    guarded with a plain ``threading.Lock`` and delivery to each
    subscriber's queue is marshalled back onto that subscriber's own
    event loop with ``call_soon_threadsafe`` rather than assuming
    ``emit`` runs on the loop that created the queue.
    """

    def __init__(self, capacity: int = LOG_BUFFER_SIZE) -> None:
        super().__init__()
        self._buffer: deque = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._subscribers: dict[asyncio.Queue, asyncio.AbstractEventLoop] = {}
        self.setLevel(logging.DEBUG)

    def emit(self, record: logging.LogRecord) -> None:
        # Guard against a feedback loop: if this module's own logger
        # (e.g. a warning logged from unsubscribe/emit below) fed back
        # into this handler, it would recurse/spam the buffer.
        if record.name == __name__:
            return
        try:
            entry = _record_to_entry(record)
        except Exception:  # pragma: no cover - logging must never raise
            return

        with self._lock:
            self._buffer.append(entry)
            subscribers = list(self._subscribers.items())

        for queue, loop in subscribers:
            try:
                loop.call_soon_threadsafe(self._put_nowait_safe, queue, entry)
            except Exception:
                # The subscriber's loop may already be closed (client
                # disconnected mid-shutdown) -- never let logging raise.
                continue

    @staticmethod
    def _put_nowait_safe(queue: asyncio.Queue, entry: dict) -> None:
        try:
            queue.put_nowait(entry)
        except asyncio.QueueFull:
            # Slow subscriber: drop the oldest entry to make room rather
            # than blocking or losing the newest one silently.
            try:
                queue.get_nowait()
                queue.put_nowait(entry)
            except Exception:
                pass

    def snapshot(self) -> list:
        with self._lock:
            return list(self._buffer)

    def subscribe(self) -> asyncio.Queue:
        """Register a new subscriber and return its queue.

        Must be called from within a running event loop -- the loop is
        captured so ``emit`` (which may run on a different thread) can
        safely deliver into this queue via ``call_soon_threadsafe``.
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        loop = asyncio.get_running_loop()
        with self._lock:
            self._subscribers[queue] = loop
        return queue

    def subscribe_with_snapshot(self) -> tuple[list, asyncio.Queue]:
        """Atomically capture the backlog and register a new subscriber.

        Taking the backlog copy and registering the subscriber under a
        single lock hold means a record emitted concurrently with this
        call is either already in the backlog (if it was appended
        before this call acquired the lock) or will be delivered live
        through the queue (if the subscriber was registered first) --
        never both, and never neither.
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        loop = asyncio.get_running_loop()
        with self._lock:
            backlog = list(self._buffer)
            self._subscribers[queue] = loop
        return backlog, queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.pop(queue, None)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def attach_to_root(self) -> None:
        """Attach this handler to the root logger, idempotently.

        Detaches (removes + clears subscribers of) any previously-
        attached ``LogStreamHandler`` first so repeated ``create_app()``
        calls (as happens across the test suite, and any future
        re-init) don't stack multiple handlers on the root logger,
        which would otherwise emit every record N times and leak old
        handlers/subscribers forever.
        """
        root = logging.getLogger()
        for existing in list(root.handlers):
            if isinstance(existing, LogStreamHandler):
                existing.detach()
        root.addHandler(self)

    def detach(self) -> None:
        logging.getLogger().removeHandler(self)
        with self._lock:
            self._subscribers.clear()


router = APIRouter()


@router.websocket("/ws/logs")
async def stream_logs(websocket: WebSocket) -> None:
    username = await require_login_ws(websocket)
    if not username:
        await websocket.close(code=1008)
        return

    handler: LogStreamHandler = websocket.app.state.log_stream
    await websocket.accept()
    backlog, queue = handler.subscribe_with_snapshot()
    try:
        await websocket.send_json({"type": "backlog", "entries": backlog})

        receive_task = asyncio.ensure_future(websocket.receive())
        get_task = asyncio.ensure_future(queue.get())
        try:
            while True:
                done, _ = await asyncio.wait(
                    {receive_task, get_task}, return_when=asyncio.FIRST_COMPLETED
                )
                if receive_task in done:
                    # Client sent something (or disconnected) -- either
                    # way this websocket handler's only job is to push,
                    # so just check for disconnect and re-arm.
                    message = receive_task.result()
                    if message.get("type") == "websocket.disconnect":
                        break
                    receive_task = asyncio.ensure_future(websocket.receive())
                if get_task in done:
                    entry = get_task.result()
                    await websocket.send_json({"type": "log", "entry": entry})
                    get_task = asyncio.ensure_future(queue.get())
        finally:
            for task in (receive_task, get_task):
                if not task.done():
                    task.cancel()
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        handler.unsubscribe(queue)
