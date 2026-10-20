"""Session-cookie authentication for RuuviHub's web UI.

Single admin account (decision #2) — no roles, no multi-user. Sessions
are signed with the settings-stored ``session_secret`` and persist for
30 days (decision #16); logging out clears the cookie immediately. A
simple escalating-delay throttle (decision #10) slows down repeated
failed login attempts instead of a full lockout/reset-email flow. The
throttle is keyed per client (fixed a global-lockout DoS: previously any
unauthenticated client could keep the delay maxed out for everyone).
"""

import hashlib
import hmac
import time
from typing import Optional, Union

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, WebSocket
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from settings import SettingsStore

SESSION_COOKIE_NAME = "ruuvihub_session"
SESSION_MAX_AGE_SECONDS = 30 * 24 * 60 * 60  # 30 days, decision #16
_SESSION_SALT = "ruuvihub-session"
_MAX_THROTTLE_DELAY_SECONDS = 10  # cap the escalating delay, decision #10
_MAX_TRACKED_CLIENTS = 1000  # bounded cleanup: cap memory use for per-client state
_DEFAULT_THROTTLE_KEY = "_default"
CSRF_HEADER_NAME = "X-CSRF-Token"
CSRF_FORM_FIELD = "csrf_token"

router = APIRouter()


class LoginThrottle:
    """Escalating-delay throttle for failed login attempts (decision #10),
    keyed per client so one abusive host can't lock out every other host.

    Each consecutive failure *for a given key* adds another second of
    required wait (capped at ``_MAX_THROTTLE_DELAY_SECONDS``), reset on
    that key's next success. State is bounded: entries whose delay has
    already elapsed are dropped opportunistically, and the number of
    tracked keys is capped at ``_MAX_TRACKED_CLIENTS`` (evicting the
    least-escalated entries first) so an attacker can't grow this dict
    without bound by spoofing many source addresses.
    """

    def __init__(self) -> None:
        self._state: dict[str, tuple[int, float]] = {}

    def _cleanup(self) -> None:
        now = time.monotonic()
        expired = [key for key, (_, next_at) in self._state.items() if next_at <= now]
        for key in expired:
            self._state.pop(key, None)
        if len(self._state) > _MAX_TRACKED_CLIENTS:
            by_urgency = sorted(self._state.items(), key=lambda item: item[1][1])
            for key, _ in by_urgency[: len(self._state) - _MAX_TRACKED_CLIENTS]:
                self._state.pop(key, None)

    def seconds_until_allowed(self, key: str = _DEFAULT_THROTTLE_KEY) -> float:
        _, next_allowed_at = self._state.get(key, (0, 0.0))
        return max(0.0, next_allowed_at - time.monotonic())

    def record_failure(self, key: str = _DEFAULT_THROTTLE_KEY) -> None:
        failures, _ = self._state.get(key, (0, 0.0))
        failures += 1
        delay = min(failures, _MAX_THROTTLE_DELAY_SECONDS)
        self._state[key] = (failures, time.monotonic() + delay)
        self._cleanup()

    def record_success(self, key: str = _DEFAULT_THROTTLE_KEY) -> None:
        self._state.pop(key, None)


def _client_key(request: Request) -> str:
    """Best-effort per-client throttle key (source IP)."""
    if request.client is not None and request.client.host:
        return request.client.host
    return _DEFAULT_THROTTLE_KEY


class RedirectToLogin(Exception):
    """Raised by require_login_page when there's no valid session.

    A FastAPI dependency can't directly return a redirect response, so
    this is turned into a real 303 redirect by an exception handler
    registered in web/app.py.
    """


def _serializer(session_secret: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(session_secret, salt=_SESSION_SALT)


def create_session_cookie(response: Response, username: str, session_secret: str, secure: bool = False) -> None:
    """Sign and set the persistent session cookie for a successful login.

    ``secure`` should be True whenever HTTPS is the effective transport
    (see ``web.app._https_effectively_enabled``) so the browser never
    sends this cookie over a plain-HTTP connection -- e.g. an old
    ``http://host:8080`` bookmark hitting the compatibility redirect
    listener -- which would otherwise leak the session token in
    cleartext even though the real app is only ever served over TLS.
    Left False for the plain-HTTP-only debug/no-HTTPS mode, where there
    is no TLS connection for the cookie to be restricted to.
    """
    token = _serializer(session_secret).dumps(username)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=secure,
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME)


def _username_from_cookie(request: Union[Request, WebSocket], session_secret: str) -> Optional[str]:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token or not session_secret:
        return None
    try:
        return _serializer(session_secret).loads(token, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None


async def get_current_username(request: Union[Request, WebSocket]) -> Optional[str]:
    """Return the logged-in username from the session cookie, or None.

    Accepts a WebSocket as well as a Request -- both expose the same
    ``.app``/``.cookies`` surface used here -- so this can also back the
    WebSocket auth check in ``require_login_ws``.
    """
    settings_store: SettingsStore = request.app.state.settings_store
    settings = await settings_store.get()
    username = _username_from_cookie(request, settings.session_secret)
    if username is None or username != settings.ui_username:
        # A username mismatch means the account was renamed since this
        # cookie was issued – treat the old session as logged out.
        return None
    return username


def compute_csrf_token(session_secret: str, username: str) -> str:
    """Derive a per-session CSRF token from the session secret + username.

    Deterministic (no extra server-side storage needed): any request
    carrying a valid session cookie can recompute the same value, and an
    attacker forging a cross-site request has no way to read/derive it
    (SameSite=Lax alone doesn't stop a malicious origin hosted on the
    same site, e.g. another service on the same host but a different
    port, from replaying the session cookie in an auto-submitted form).
    """
    return hmac.new(
        session_secret.encode("utf-8"),
        f"csrf:{username}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


async def get_csrf_token(request: Request) -> str:
    """Return the current session's CSRF token, or "" if not logged in.

    Used to embed the token in rendered templates (hidden form field /
    meta tag) so forms and AJAX requests can echo it back on mutations.
    """
    settings_store: SettingsStore = request.app.state.settings_store
    settings = await settings_store.get()
    username = _username_from_cookie(request, settings.session_secret)
    if username is None or username != settings.ui_username:
        return ""
    return compute_csrf_token(settings.session_secret, username)


async def verify_csrf(request: Request, submitted: str) -> None:
    """Raise HTTP 403 unless ``submitted`` matches the current session's
    CSRF token. Applied to every state-changing (POST) route so a
    malicious cross-site form/script can't ride the admin's session
    cookie to submit config changes, renames, or logout.
    """
    expected = await get_csrf_token(request)
    if not expected or not submitted or not hmac.compare_digest(submitted, expected):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token")


async def require_login_page(request: Request) -> str:
    """Dependency for HTML page routes: redirect to /login if not authenticated."""
    username = await get_current_username(request)
    if username is None:
        raise RedirectToLogin()
    return username


async def require_login_api(request: Request) -> str:
    """Dependency for JSON API routes: 401 (no redirect) if not authenticated."""
    username = await get_current_username(request)
    if username is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return username


async def require_login_ws(websocket: WebSocket) -> Optional[str]:
    """WebSocket equivalent of require_login_page/require_login_api.

    Not a FastAPI ``Depends`` dependency: a WebSocket handshake can't be
    redirected or answered with a 401 response the way an HTTP request
    can, so the route itself must check the return value and close the
    connection (code 1008, policy violation) if it's falsy, before
    accepting the socket.
    """
    return await get_current_username(websocket)


@router.get("/login")
async def login_form(request: Request):
    if await get_current_username(request) is not None:
        return RedirectResponse("/", status_code=303)
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "login.html", {})


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    templates = request.app.state.templates
    throttle: LoginThrottle = request.app.state.login_throttle
    throttle_key = _client_key(request)

    wait = throttle.seconds_until_allowed(throttle_key)
    if wait > 0:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": f"Too many attempts – try again in {int(wait) + 1}s."},
            status_code=429,
        )

    settings_store: SettingsStore = request.app.state.settings_store
    settings = await settings_store.get()

    if not settings.verify_login(username, password):
        throttle.record_failure(throttle_key)
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Invalid username or password."},
            status_code=401,
        )

    throttle.record_success(throttle_key)
    response = RedirectResponse("/", status_code=303)
    create_session_cookie(
        response, settings.ui_username, settings.session_secret, secure=request.app.state.https_enabled
    )
    return response


@router.post("/logout")
async def logout(request: Request, csrf_token: str = Form("")):
    await verify_csrf(request, csrf_token)
    response = RedirectResponse("/login", status_code=303)
    clear_session_cookie(response)
    return response
