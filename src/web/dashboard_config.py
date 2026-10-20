"""Admin-only dashboard preferences, saved independently of system settings."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from dashboard_preferences import DASHBOARD_METRICS, DEFAULT_DASHBOARD_METRICS, MAX_DASHBOARD_CHANGE_HOURS
from mac_utils import compact_upper
from web.auth import require_login_page, verify_csrf
from web.config import _known_macs, _tag_rows

router = APIRouter()


@router.get("/config/dashboard")
async def dashboard_config_page(request: Request, _username: str = Depends(require_login_page)):
    settings = await request.app.state.settings_store.get()
    discovered = await request.app.state.storage.known_macs()
    tags = _tag_rows(settings.tag_addresses, settings.tag_names, [*discovered, *settings.dashboard_hidden_tags])
    hidden = {compact_upper(mac) for mac in settings.dashboard_hidden_tags}
    for tag in tags:
        tag["visible"] = compact_upper(tag["mac"]) not in hidden
    return request.app.state.templates.TemplateResponse(request, "dashboard_config.html", {
        "settings": settings, "metrics": DASHBOARD_METRICS, "tags": tags,
        "max_change_hours": MAX_DASHBOARD_CHANGE_HOURS,
        "saved": request.query_params.get("saved") == "1",
        "error": request.query_params.get("error") == "invalid_values",
        "using_default_credentials": settings.is_using_default_credentials(),
    })


@router.post("/config/dashboard")
async def save_dashboard_config(request: Request, _username: str = Depends(require_login_page)):
    form = await request.form()
    await verify_csrf(request, str(form.get("csrf_token", "")))
    store = request.app.state.settings_store
    settings = await store.get()
    discovered = await request.app.state.storage.known_macs()
    available_tags = _known_macs(settings.tag_addresses, settings.tag_names, [*discovered, *settings.dashboard_hidden_tags])
    known_tags = form.getlist("known_tags")
    visible_tags = form.getlist("visible_tags")
    visible_metrics = form.getlist("visible_metrics")
    try:
        refresh = int(form.get("dashboard_refresh_seconds", ""))
        hours = int(form.get("dashboard_change_hours", ""))
        if refresh < 1 or not 1 <= hours <= MAX_DASHBOARD_CHANGE_HOURS:
            raise ValueError()
        if any(metric not in DEFAULT_DASHBOARD_METRICS for metric in visible_metrics):
            raise ValueError()
        if any(mac not in available_tags for mac in known_tags) or any(mac not in known_tags for mac in visible_tags):
            raise ValueError()
    except (TypeError, ValueError):
        return RedirectResponse("/config/dashboard?error=invalid_values", status_code=303)

    # Change only sensors present in the submitted form; newly discovered tags
    # keep their default visibility and omitted preferences remain intact.
    submitted = {compact_upper(mac) for mac in known_tags}
    visible = {compact_upper(mac) for mac in visible_tags}
    hidden = [mac for mac in settings.dashboard_hidden_tags if compact_upper(mac) not in submitted]
    hidden.extend(mac for mac in known_tags if compact_upper(mac) not in visible)
    await store.update(
        dashboard_refresh_seconds=refresh, dashboard_change_hours=hours,
        dashboard_visible_metrics=list(dict.fromkeys(visible_metrics)),
        dashboard_hidden_tags=list(dict.fromkeys(hidden)),
        dashboard_show_storage_info="dashboard_show_storage_info" in form,
    )
    return RedirectResponse("/config/dashboard?saved=1", status_code=303)
