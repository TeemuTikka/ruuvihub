"""Config page routes for RuuviHub's embedded web UI."""

import asyncio
import logging
import re
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from history_backfill import run_backfill_for_known_tags
from settings import hash_password, UPLOAD_DESTINATIONS
from uploader import validate_azure_blob_container_url
from web.auth import (
    create_session_cookie,
    require_login_api,
    require_login_page,
    verify_csrf,
)

router = APIRouter()
logger = logging.getLogger(__name__)

_ERROR_MESSAGES = {
    "password_mismatch": "New password and confirmation do not match.",
    "invalid_values": "One or more settings values were invalid (intervals/timeouts/limits must be positive numbers, retention must not be negative).",
    "missing_endpoint": "Cannot enable cloud upload without an endpoint URL.",
    "invalid_destination": "Select a supported upload destination.",
    "missing_azure_container": "Enter an Azure Blob container SAS URL before enabling upload.",
    "invalid_azure_container": "Enter an HTTPS Azure Blob container SAS URL, including its SAS token.",
}

# Bounds mirrored from settings.validate_fields -- enforced here too so a
# bad submission gets a friendly form error/redirect instead of either a
# 500 (unhandled ValueError from int()) or a silently-ignored update.
_POSITIVE_INT_FIELDS = (
    "scan_interval_seconds",
    "scan_timeout_seconds",
    "upload_interval_seconds",
    "max_entries_per_tag",
    "history_backfill_interval_seconds",
    "history_backfill_failure_threshold",
    "history_backfill_timeout_seconds",
)
_NON_NEGATIVE_INT_FIELDS = ("db_retention_days",)


def _parse_tag_addresses(raw: str) -> list[str]:
    return [part.strip() for part in re.split(r"[\r\n,]+", raw) if part.strip()]


def _known_macs(
    tag_addresses: list[str], tag_names: dict[str, str], discovered_macs: list[str] = ()
) -> list[str]:
    macs: list[str] = []
    seen: set[str] = set()
    for mac in [*tag_addresses, *tag_names.keys(), *discovered_macs]:
        if mac not in seen:
            seen.add(mac)
            macs.append(mac)
    return macs


def _tag_field_name(mac: str) -> str:
    return f"tag_name_{mac.replace(':', '_')}"


def _disabled_backfill_tags(disabled_tags: dict[str, str]) -> list[dict[str, str]]:
    return [
        {"mac": mac, "reason": reason, "field_suffix": mac.replace(":", "_")}
        for mac, reason in disabled_tags.items()
    ]


def _tag_rows(
    tag_addresses: list[str], tag_names: dict[str, str], discovered_macs: list[str] = ()
) -> list[dict[str, str]]:
    return [
        {"mac": mac, "field_name": _tag_field_name(mac), "name": tag_names.get(mac, "")}
        for mac in _known_macs(tag_addresses, tag_names, discovered_macs)
    ]


def _parse_positive_ints(form, fields: tuple[str, ...], current) -> dict:
    """Parse+validate a set of form fields as positive integers.

    Raises ValueError (caught by the caller) on anything that isn't a
    valid positive integer, so callers get one place to redirect with a
    friendly error instead of a 500 or a silently-clamped value.
    """
    parsed = {}
    for name in fields:
        raw = form.get(name, getattr(current, name))
        value = int(raw)
        if value <= 0:
            raise ValueError(f"{name} must be positive")
        parsed[name] = value
    return parsed


def _parse_non_negative_ints(form, fields: tuple[str, ...], current) -> dict:
    parsed = {}
    for name in fields:
        raw = form.get(name, getattr(current, name))
        value = int(raw)
        if value < 0:
            raise ValueError(f"{name} must not be negative")
        parsed[name] = value
    return parsed


@router.get("/config")
async def config_page(
    request: Request,
    _username: str = Depends(require_login_page),
):
    settings = await request.app.state.settings_store.get()
    storage = request.app.state.storage
    discovered_macs = await storage.known_macs()
    error = _ERROR_MESSAGES.get(request.query_params.get("error", ""))
    return request.app.state.templates.TemplateResponse(
        request,
        "config.html",
        {
            "settings": settings,
            "tag_addresses_text": "\n".join(settings.tag_addresses),
            "tag_rows": _tag_rows(settings.tag_addresses, settings.tag_names, discovered_macs),
            "disabled_backfill_tags": _disabled_backfill_tags(settings.history_backfill_disabled_tags),
            "saved": request.query_params.get("saved") == "1",
            "syncing": request.query_params.get("syncing") == "1",
            "backfill_sync_status": request.app.state.backfill_sync_status,
            "error": error,
            "using_default_credentials": settings.is_using_default_credentials(),
        },
    )


@router.post("/config")
async def save_config(
    request: Request,
    _username: str = Depends(require_login_page),
):
    form = await request.form()
    await verify_csrf(request, str(form.get("csrf_token", "")))

    settings_store = request.app.state.settings_store
    storage = request.app.state.storage
    current_settings = await settings_store.get()
    discovered_macs = await storage.known_macs()

    new_password = str(form.get("new_password", ""))
    confirm_new_password = str(form.get("confirm_new_password", ""))
    if new_password and new_password != confirm_new_password:
        return RedirectResponse("/config?error=password_mismatch", status_code=303)

    cloud_upload_enabled = form.get("cloud_upload_enabled") is not None
    scan_enabled = form.get("scan_enabled") is not None
    history_backfill_enabled = form.get("history_backfill_enabled") is not None
    history_backfill_on_startup = form.get("history_backfill_on_startup") is not None
    history_backfill_periodic = form.get("history_backfill_periodic") is not None
    cloud_endpoint_url = str(form.get("cloud_endpoint_url", current_settings.cloud_endpoint_url)).strip()
    destination = str(form.get("cloud_upload_destination", current_settings.cloud_upload_destination))
    if destination not in UPLOAD_DESTINATIONS:
        return RedirectResponse("/config?error=invalid_destination", status_code=303)
    azure_container = str(form.get("azure_blob_container_url", "")).strip() or current_settings.azure_blob_container_url
    if destination == "azure_blob":
        if cloud_upload_enabled and not azure_container:
            return RedirectResponse("/config?error=missing_azure_container", status_code=303)
        if azure_container:
            try:
                validate_azure_blob_container_url(azure_container)
            except ValueError:
                return RedirectResponse("/config?error=invalid_azure_container", status_code=303)
    if destination == "http" and cloud_upload_enabled and not cloud_endpoint_url:
        return RedirectResponse("/config?error=missing_endpoint", status_code=303)

    try:
        updates = {
            **_parse_positive_ints(form, _POSITIVE_INT_FIELDS, current_settings),
            **_parse_non_negative_ints(form, _NON_NEGATIVE_INT_FIELDS, current_settings),
        }
    except (TypeError, ValueError):
        return RedirectResponse("/config?error=invalid_values", status_code=303)

    updates.update(
        {
            "tag_addresses": _parse_tag_addresses(str(form.get("tag_addresses", ""))),
            "scan_enabled": scan_enabled,
            "cloud_upload_enabled": cloud_upload_enabled,
            "cloud_upload_destination": destination,
            "cloud_endpoint_url": cloud_endpoint_url,
            "azure_blob_container_url": azure_container,
            "cloud_auth_header_name": str(
                form.get("cloud_auth_header_name", current_settings.cloud_auth_header_name)
            ).strip(),
            "ui_username": str(form.get("ui_username", current_settings.ui_username)).strip()
            or current_settings.ui_username,
            "history_backfill_enabled": history_backfill_enabled,
            "history_backfill_on_startup": history_backfill_on_startup,
            "history_backfill_periodic": history_backfill_periodic,
        }
    )

    tag_names: dict[str, str] = {}
    for mac in _known_macs(current_settings.tag_addresses, current_settings.tag_names, discovered_macs):
        submitted_name = str(form.get(_tag_field_name(mac), "")).strip()
        if submitted_name:
            tag_names[mac] = submitted_name
    updates["tag_names"] = tag_names

    cloud_auth_header_value = str(form.get("cloud_auth_header_value", ""))
    if cloud_auth_header_value.strip():
        updates["cloud_auth_header_value"] = cloud_auth_header_value

    rotate_session = False
    if new_password:
        updates["ui_password_hash"] = hash_password(new_password)
        # Changing the password must invalidate every previously issued
        # session cookie (otherwise a stolen/leaked 30-day cookie survives
        # a password reset); rotating the signing secret does that.
        updates["session_secret"] = secrets.token_hex(32)
        rotate_session = True

    updated = await settings_store.update(**updates)
    response = RedirectResponse("/config?saved=1", status_code=303)
    if rotate_session:
        # Re-issue a fresh, validly-signed cookie for the admin performing
        # the change so they aren't logged out by their own password reset.
        create_session_cookie(
            response, updated.ui_username, updated.session_secret, secure=request.app.state.https_enabled
        )
    return response


@router.post("/config/reenable-backfill")
async def reenable_backfill(
    request: Request,
    _username: str = Depends(require_login_page),
):
    """Clear a single tag's auto-disabled-from-backfill state.

    A separate small form/route (rather than folding this into the main
    /config POST) so re-enabling one tag doesn't require resubmitting the
    entire settings form, and can't be accidentally triggered by an
    unrelated save.
    """
    form = await request.form()
    await verify_csrf(request, str(form.get("csrf_token", "")))

    mac = str(form.get("mac", "")).strip()
    if mac:
        await request.app.state.settings_store.set_backfill_disabled(mac, None)
    return RedirectResponse("/config?saved=1", status_code=303)


@router.post("/config/sync-now")
async def sync_now(
    request: Request,
    _username: str = Depends(require_login_page),
):
    """Trigger an immediate one-off history backfill pass.

    Unlike the automatic history_backfill_loop (which only runs if
    history_backfill_enabled is on), this lets an admin manually pull
    history right now regardless of that setting -- e.g. right after
    fixing a flaky tag, without waiting for the next scheduled cycle.

    Runs as a detached background task (not awaited here) since a full
    pass can take up to ~60s per tag; the request returns immediately
    and the Config page shows simple running/last-synced status instead
    of blocking the browser for however long the sync takes. Guarded by
    ``backfill_sync_status["running"]`` so a second click while one is
    already in flight is a no-op rather than starting a second pass
    that would fight over the BLE radio with the first.
    """
    form = await request.form()
    await verify_csrf(request, str(form.get("csrf_token", "")))

    app_state = request.app.state
    if not app_state.backfill_sync_status["running"]:
        app_state.backfill_sync_status["running"] = True
        asyncio.create_task(
            _run_manual_sync(app_state.settings_store, app_state.storage, app_state.ble_lock, app_state)
        )
    return RedirectResponse("/config?syncing=1", status_code=303)


async def _run_manual_sync(settings_store, storage, ble_lock, app_state) -> None:
    """Background task body for the "Sync history now" button.

    Holds ``ble_lock`` for the whole pass, same as history_backfill_loop,
    so it never runs BLE operations at the same time as scan_loop.
    """
    try:
        settings = await settings_store.get()
        async with ble_lock:
            await run_backfill_for_known_tags(settings, storage, settings_store)
    except Exception:
        logger.exception("Manual 'sync history now' pass failed")
    finally:
        app_state.backfill_sync_status["running"] = False
        app_state.backfill_sync_status["last_synced_at"] = datetime.now(timezone.utc).isoformat()


@router.post("/api/config/test-upload")
async def test_upload_connection(
    request: Request,
    cloud_endpoint_url: str = Form(""),
    cloud_auth_header_name: str = Form(""),
    cloud_auth_header_value: str = Form(""),
    cloud_upload_destination: str | None = Form(None),
    azure_blob_container_url: str = Form(""),
    _username: str = Depends(require_login_api),
):
    await verify_csrf(request, request.headers.get("X-CSRF-Token", ""))

    settings = await request.app.state.settings_store.get()
    # Test the submitted candidate values directly without persisting
    # them: if cloud upload were already enabled, saving first would let
    # the background upload loop immediately send real pending readings
    # to an unverified endpoint, and a failed test would clobber a
    # previously-working configuration.
    header_value = cloud_auth_header_value if cloud_auth_header_value.strip() else settings.cloud_auth_header_value
    success, message = await request.app.state.uploader.test_upload(
        endpoint_url=cloud_endpoint_url.strip(),
        auth_header_name=cloud_auth_header_name.strip(),
        auth_header_value=header_value,
        destination=cloud_upload_destination,
        azure_blob_container_url=azure_blob_container_url.strip() or settings.azure_blob_container_url,
    )
    return {"success": success, "message": message}
