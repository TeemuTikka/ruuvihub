"""Dashboard page with a live-updating card grid of the latest reading per tag."""

from datetime import datetime, time, timedelta, timezone
from typing import Optional
import math

from fastapi import APIRouter, Request

from settings import Settings, SettingsStore
from mac_utils import compact_upper
from storage import Storage, TEMPERATURE_CHANGE_TOLERANCE_SECONDS

router = APIRouter()


def _format_bytes(value: Optional[int]) -> str:
    """Render a byte count for the server-rendered page.

    Mirrored in ``formatBytes`` (static/dashboard.js) for the client-side
    poll updates -- keep both in sync if this formatting ever changes.
    """
    if value is None:
        return "Unavailable"
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{int(amount)} B" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} TB"


def _is_stale(timestamp: Optional[str], scan_interval_seconds: int) -> bool:
    """A reading is stale if it's older than 2x the scan interval."""
    if not timestamp:
        return True
    try:
        seen_at = datetime.fromisoformat(timestamp)
    except ValueError:
        return True
    if seen_at.tzinfo is None:
        seen_at = seen_at.replace(tzinfo=timezone.utc)
    age_seconds = (datetime.now(timezone.utc) - seen_at).total_seconds()
    return age_seconds > max(scan_interval_seconds, 1) * 2


async def _build_dashboard_cards(storage: Storage, settings: Settings) -> list[dict]:
    """Build dashboard cards, keeping latest-reading work proportional to tags."""
    records = await storage.get_latest_readings()
    hidden_tags = {compact_upper(mac) for mac in settings.dashboard_hidden_tags}
    records = [record for record in records if compact_upper(record.get("mac", "")) not in hidden_tags]
    names = {}
    for key, name in settings.tag_names.items():
        names.setdefault(compact_upper(key), name)

    # Keep local wall times naive until each boundary is converted to UTC.
    # astimezone() without a target produces a fixed offset, which must not
    # be reused for yesterday when a daylight-saving transition intervenes.
    now = datetime.now()
    now_utc = now.astimezone(timezone.utc)
    today = now.date()
    day_start = datetime.combine(today, time(7))
    day_end = datetime.combine(today, time(22))
    if now >= day_end:
        # Already past tonight's day/night boundary: the night window is
        # still in progress, running from day_end up to now.
        night_start = day_end
        night_end = now
    else:
        # Either still in yesterday's night (before day_start) or inside
        # today's daytime window: last night's window is the completed one.
        night_start = datetime.combine(today - timedelta(days=1), time(22))
        night_end = day_start
    summaries = await storage.get_temperature_summaries(
        [record["mac"] for record in records if record.get("mac") is not None],
        day_start.astimezone(timezone.utc).isoformat(),
        min(now, day_end).astimezone(timezone.utc).isoformat(),
        night_start.astimezone(timezone.utc).isoformat(),
        night_end.astimezone(timezone.utc).isoformat(),
        now_utc.isoformat(),
        settings.dashboard_change_hours,
    )

    cards = []
    for record in records:
        mac = record.get("mac")
        if mac is None:
            continue

        summary = summaries[mac].copy()
        hourly_temperature = summary.pop("hour_temperature")
        temperature_change = None
        current_temperature = record.get("temperature")
        if (type(current_temperature) in (int, float)
                and type(hourly_temperature) in (int, float)
                and math.isfinite(current_temperature) and math.isfinite(hourly_temperature)):
            try:
                seen_at = datetime.fromisoformat(record.get("timestamp", ""))
                if seen_at.tzinfo is None:
                    seen_at = seen_at.replace(tzinfo=timezone.utc)
                age = (now_utc - seen_at).total_seconds()
                if 0 <= age <= TEMPERATURE_CHANGE_TOLERANCE_SECONDS:
                    temperature_change = round(current_temperature - hourly_temperature, 1) or 0.0
            except (TypeError, ValueError):
                pass

        cards.append(
            {
                "mac": mac,
                "name": names.get(compact_upper(mac), mac),
                "timestamp": record.get("timestamp"),
                "temperature": record.get("temperature"),
                "humidity": record.get("humidity"),
                "pressure": record.get("pressure"),
                "battery": record.get("battery"),
                "rssi": record.get("rssi"),
                **summary,
                "temperature_change": temperature_change,
                "stale": _is_stale(record.get("timestamp"), settings.scan_interval_seconds),
            }
        )
    cards.sort(key=lambda c: c["name"].lower())
    return cards


@router.get("/")
async def dashboard_page(request: Request):
    storage: Storage = request.app.state.storage
    settings_store: SettingsStore = request.app.state.settings_store
    settings = await settings_store.get()
    cards = await _build_dashboard_cards(storage, settings)
    storage_stats = None
    if settings.dashboard_show_storage_info:
        storage_stats = await storage.get_storage_stats()
        storage_stats["database_size_display"] = _format_bytes(
            storage_stats["database_size_bytes"]
        )
        storage_stats["disk_free_display"] = _format_bytes(storage_stats["disk_free_bytes"])
    return request.app.state.templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "cards": cards,
            "storage_stats": storage_stats,
            "refresh_seconds": settings.dashboard_refresh_seconds,
            "visible_metrics": settings.dashboard_visible_metrics,
            "change_hours": settings.dashboard_change_hours,
            "using_default_credentials": settings.is_using_default_credentials(),
        },
    )


@router.get("/api/current")
async def api_current(request: Request):
    storage: Storage = request.app.state.storage
    settings_store: SettingsStore = request.app.state.settings_store
    settings = await settings_store.get()
    response = {
        "cards": await _build_dashboard_cards(storage, settings),
        "dashboard_options": {
            "visible_metrics": settings.dashboard_visible_metrics,
            "change_hours": settings.dashboard_change_hours,
            "refresh_seconds": settings.dashboard_refresh_seconds,
        },
    }
    if settings.dashboard_show_storage_info:
        response["storage"] = await storage.get_storage_stats()
    return response
