"""FastAPI application factory for RuuviHub's embedded web UI.

Kept as an explicit factory (``create_app``) rather than a module-level
``FastAPI()`` instance so the app never reaches for global state — it
always receives its ``Storage`` and ``SettingsStore`` instances
explicitly, the same way ``scan_loop``/``upload_loop`` are wired in
``main.py``. This lets the web server share the exact same in-memory
Storage/SettingsStore the scan/upload loops use (no separate DB polling
process, no IPC), and keeps the app trivially testable with a
throwaway Storage/SettingsStore per test.

Routes (login/logout, dashboard, per-sensor history, config page) are
registered by later modules in this package; this skeleton wires up the
app object, shared state, template/static mounting, and a health-check
endpoint used by ``run_web_server``/tests to confirm the server started.
"""

import logging
import os
from pathlib import Path
from typing import Optional

import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.gzip import GZipMiddleware

from settings import SettingsStore
from datetime_formats import format_timestamp
from storage import Storage
from tls import ensure_self_signed_cert
from uploader import Uploader
from version import get_version
from web.auth import LoginThrottle, RedirectToLogin, get_csrf_token, router as auth_router
from web.config import router as config_router
from web.dashboard import router as dashboard_router
from web.dashboard_config import router as dashboard_config_router
from web.logstream import LogStreamHandler, router as logstream_router
from web.sensor import router as sensor_router

_WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = _WEB_DIR / "templates"
STATIC_DIR = _WEB_DIR / "static"
# Repo root (two levels up from src/web/), matching how storage.py/main.py
# resolve db_path/log_path defaults relative to main.py's directory.
_REPO_ROOT = _WEB_DIR.parent.parent
_DEFAULT_CERT_PATH = _REPO_ROOT / "certs" / "ruuvihub.crt"
_DEFAULT_KEY_PATH = _REPO_ROOT / "certs" / "ruuvihub.key"

# Set by the VS Code launch configs (see .vscode/launch.json) so F5/local
# debugging always gets plain HTTP on localhost -- no self-signed cert
# warning to click through and no port confusion for serverReadyAction --
# regardless of what settings.local.json has saved. Never set this in a
# real deployment; it silently overrides the config-page HTTPS toggle.
_ENV_DISABLE_HTTPS = "RUUVIHUB_DISABLE_HTTPS"

logger = logging.getLogger(__name__)


def _https_effectively_enabled(settings_https_enabled: bool) -> bool:
    """Resolve whether HTTPS should actually start, honoring the debug override."""
    if os.environ.get(_ENV_DISABLE_HTTPS, "").strip().lower() in ("1", "true", "yes"):
        logger.info(
            "%s is set – forcing plain HTTP (ignoring https_enabled setting), "
            "for local debugging only",
            _ENV_DISABLE_HTTPS,
        )
        return False
    return settings_https_enabled


def create_app(
    storage: Storage,
    settings_store: SettingsStore,
    ble_lock: Optional[asyncio.Lock] = None,
    https_enabled: bool = False,
) -> FastAPI:
    """Build and return the RuuviHub FastAPI application.

    Args:
        storage: Shared Storage instance also used by scan_loop/upload_loop.
        settings_store: Shared SettingsStore instance also used by the
            scan/upload loops, so config-page edits apply everywhere
            without a restart (except db_path, which is file-only).
        ble_lock: Shared lock serializing BLE radio access with
            scan_loop/history_backfill_loop (see main.py). Used by the
            Config page's "Sync history now" button so a manual backfill
            never runs at the same time as passive scanning. Defaults to
            a fresh, unshared lock when not provided (e.g. in tests that
            don't exercise scan_loop concurrency), so callers that don't
            care about this don't need to construct one themselves.
        https_enabled: Whether this app instance is effectively served
            over HTTPS (see ``_https_effectively_enabled``). Used to mark
            the session cookie ``Secure`` so it's never sent in
            cleartext to the plain-HTTP compatibility listener (old
            bookmarks to ``http://host:8080/...``) -- browsers scope
            cookies by host, not port, so without this the session token
            would otherwise leak over that unencrypted connection.
            Defaults to False so tests using plain ``TestClient`` (which
            talks HTTP) can still read the session cookie back.
    """
    app = FastAPI(title="RuuviHub")

    # Stashed on app.state (not module globals) so route modules
    # registered elsewhere (auth, dashboard, sensor, config) can reach
    # the same shared instances via `request.app.state`.
    app.state.storage = storage
    app.state.settings_store = settings_store
    app.state.uploader = Uploader(settings_store)
    app.state.ble_lock = ble_lock if ble_lock is not None else asyncio.Lock()
    app.state.https_enabled = https_enabled
    # Tracks the Config page's manual "Sync history now" button: whether a
    # sync is currently in flight (to avoid double-triggering) and when
    # the last one finished, so the page can show simple status text
    # without a full progress UI.
    app.state.backfill_sync_status = {"running": False, "last_synced_at": None}
    app.state.templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    # Exposed as a Jinja global (not per-route context) so every template
    # can render it in a footer without each route handler wiring it in.
    app.state.templates.env.globals["app_version"] = get_version()
    # Changed assets get new URLs so cached scripts cannot keep old formatting.
    app.state.templates.env.globals["static_url"] = (
        lambda filename: f"/static/{filename}?v={(STATIC_DIR / filename).stat().st_mtime_ns}"
    )
    app.state.templates.env.filters["format_timestamp"] = (
        lambda value: format_timestamp(value, settings_store.get_sync().datetime_format)
    )
    # Escalating-delay login throttle (decision #10); lives on app.state
    # (not a module global) so each app instance – e.g. each test – gets
    # its own independent throttle.
    app.state.login_throttle = LoginThrottle()
    # Live log stream (log panel in base.html): a ring-buffer handler
    # attached to the root logger, fanning out new records to any
    # connected /ws/logs subscriber. attach_to_root() is idempotent, so
    # repeated create_app() calls (every test in the suite) never stack
    # duplicate handlers on the root logger.
    app.state.log_stream = LogStreamHandler()
    app.state.log_stream.attach_to_root()

    # Compress only public static assets. Keep authenticated pages, APIs,
    # exports, and the live log stream outside this middleware.
    app.mount(
        "/static",
        GZipMiddleware(StaticFiles(directory=str(STATIC_DIR)), minimum_size=1000, compresslevel=6),
        name="static",
    )

    @app.middleware("http")
    async def _inject_csrf_token(request: Request, call_next):
        """Compute the current session's CSRF token once per request and
        stash it on request.state so templates can embed it in forms/meta
        tags without every route handler wiring it through explicitly."""
        request.state.csrf_token = await get_csrf_token(request)
        return await call_next(request)

    @app.exception_handler(RedirectToLogin)
    async def _redirect_to_login(request: Request, exc: RedirectToLogin):
        return RedirectResponse("/login", status_code=303)

    app.include_router(auth_router)
    app.include_router(dashboard_router)
    app.include_router(sensor_router)
    app.include_router(config_router)
    app.include_router(dashboard_config_router)
    app.include_router(logstream_router)

    @app.get("/healthz")
    async def healthz():
        """Liveness endpoint – confirms the web server task started up.

        Includes the running version (from the ``VERSION`` file baked
        into release archives) so ``update.sh`` can verify a deploy
        succeeded and operators can confirm which build a given Pi is
        on without SSH-ing in.
        """
        return {"status": "ok", "version": get_version()}

    return app


def create_redirect_app(https_port: int) -> FastAPI:
    """Build a minimal ASGI app that 308s every request to HTTPS.

    Served on the plain-HTTP port when ``https_redirect_http`` is on, so
    old bookmarks/links to ``http://host:8080/...`` still land somewhere
    useful instead of just failing once HTTPS becomes the primary
    listener. Deliberately has none of the real app's routes/state --
    it only ever redirects, so it doesn't need auth, CSRF, storage, etc.

    ``/healthz`` is the one exception: it's served directly (not
    redirected) so the *old*, pre-HTTPS ``update.sh`` shipped with
    releases before this one -- which only knows to check
    ``http://localhost:8080/healthz`` for version JSON -- can still
    verify a deploy of *this* release succeeded. A redirect response
    has an empty body, so that old script would otherwise read no
    version, treat the upgrade as failed, and roll it back.
    """
    redirect_app = FastAPI(title="RuuviHub (HTTPS redirect)")

    @redirect_app.get("/healthz")
    async def _healthz():
        return {"status": "ok", "version": get_version()}

    @redirect_app.api_route(
        "/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]
    )
    async def _redirect(request: Request, path: str):
        hostname = request.url.hostname or "localhost"
        target = f"https://{hostname}:{https_port}/{path}"
        if request.url.query:
            target = f"{target}?{request.url.query}"
        return RedirectResponse(target, status_code=308)

    return redirect_app


async def run_web_server(
    storage: Storage,
    settings_store: SettingsStore,
    ble_lock: Optional[asyncio.Lock] = None,
    host: str = "0.0.0.0",
    port: int = 8080,
) -> None:
    """Run the embedded web UI server until cancelled.

    Binds 0.0.0.0:<port> by default (decisions #1/#7) so the UI is
    reachable across the LAN, not just localhost. Uses uvicorn's Server
    API directly (rather than ``uvicorn.run()``) so each server behaves
    as a plain awaitable coroutine that fits alongside scan_loop/
    upload_loop in main.py's asyncio.gather, instead of trying to manage
    its own event loop.

    HTTPS is on by default (``https_enabled``). When enabled, this
    starts the real app over TLS on ``https_port`` -- auto-generating a
    self-signed certificate on first run if ``tls_cert_path``/
    ``tls_key_path`` aren't set to an existing user-supplied cert (see
    ``tls.ensure_self_signed_cert``) -- and, if ``https_redirect_http``
    is also on (the default), a second minimal server on the plain
    ``port`` that just 308s everything to the HTTPS listener. Setting
    ``https_enabled`` to false restores the previous plain-HTTP-only
    behavior (e.g. for a setup that terminates TLS at a reverse proxy).

    Also plain-HTTP-only, regardless of the ``https_enabled`` setting,
    whenever the ``RUUVIHUB_DISABLE_HTTPS`` env var is set (see
    ``.vscode/launch.json``) -- local debugging shouldn't require
    clicking through a self-signed cert warning or juggling two ports.
    """
    import uvicorn

    settings = await settings_store.get()
    https_enabled = _https_effectively_enabled(settings.https_enabled)
    app = create_app(storage, settings_store, ble_lock, https_enabled=https_enabled)

    if not https_enabled:
        config = uvicorn.Config(app, host=host, port=port, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()
        return

    if settings.https_redirect_http and settings.https_port == port:
        raise RuntimeError(
            f"https_port ({settings.https_port}) collides with the plain-HTTP "
            f"port ({port}) while https_redirect_http is enabled -- both "
            "listeners cannot bind the same port. Set https_port to a "
            "different value, or disable https_redirect_http."
        )

    cert_path = settings.tls_cert_path or str(_DEFAULT_CERT_PATH)
    key_path = settings.tls_key_path or str(_DEFAULT_KEY_PATH)
    ensure_self_signed_cert(cert_path, key_path)

    https_config = uvicorn.Config(
        app,
        host=host,
        port=settings.https_port,
        ssl_certfile=cert_path,
        ssl_keyfile=key_path,
        log_level="info",
    )
    servers = [uvicorn.Server(https_config)]

    if settings.https_redirect_http:
        redirect_config = uvicorn.Config(
            create_redirect_app(settings.https_port),
            host=host,
            port=port,
            log_level="warning",
        )
        servers.append(uvicorn.Server(redirect_config))

    await _serve_all(servers)


async def _serve_all(servers: list) -> None:
    """Run several ``uvicorn.Server`` instances concurrently, and if any
    one of them exits or raises, signal and await a graceful shutdown of
    every other one before returning/re-raising.

    ``asyncio.gather`` on its own propagates the first exception without
    cancelling its sibling tasks. Left unattended, a sibling server can
    keep its listening socket bound after the caller (``web_server_loop``)
    catches the error and retries starting the whole app -- so the retry
    then fails to bind that same port too, and every subsequent restart
    fails the same way.
    """
    tasks = [asyncio.create_task(server.serve()) for server in servers]
    try:
        await asyncio.gather(*tasks)
    finally:
        for server, task in zip(servers, tasks):
            if not task.done():
                server.should_exit = True
        await asyncio.gather(*tasks, return_exceptions=True)
