"""Dashboard page with a live-updating card grid of the latest reading per tag."""

from datetime import datetime, timedelta, timezone
from typing import Optional
import math

from fastapi import APIRouter, Request

from settings import Settings, SettingsStore
from mac_utils import compact_upper
from storage import Storage
from dashboard_preferences import HISTORY_HOURS, DEFAULT_HISTORY_HOURS

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


def _history_hours(value):
    try:
        hours = int(value)
        return hours if hours in HISTORY_HOURS else DEFAULT_HISTORY_HOURS
    except (ValueError, TypeError):
        return DEFAULT_HISTORY_HOURS


def _trend_path(samples, since, until, low, high):
    """Draw actual sample times, starting new SVG subpaths at missing data."""
    if low is None or high is None:
        return ""
    duration = (until - since).total_seconds()
    path = []
    previous_segment = None
    connected = False
    for sample in samples:
        value = sample["temperature"]
        if type(value) not in (int, float) or not math.isfinite(value):
            connected = False
            continue
        stamp = datetime.fromisoformat(sample["timestamp"])
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        x = 2 + (until - stamp).total_seconds() / duration * 236
        y = 32 if high == low else 2 + (high - value) / (high - low) * 60
        command = "L" if connected and previous_segment == sample["segment"] else "M"
        path.append(f"{command}{x:.2f},{y:.2f}")
        if command == "M":
            path.append(f"L{x:.2f},{y:.2f}")  # Round caps keep lone samples visible.
        connected = True
        previous_segment = sample["segment"]
    return " ".join(path)


async def _build_dashboard_cards(storage: Storage, settings: Settings, history_hours=DEFAULT_HISTORY_HOURS) -> list[dict]:
    """Build dashboard cards, keeping latest-reading work proportional to tags."""
    records = await storage.get_latest_readings()
    hidden_tags = {compact_upper(mac) for mac in settings.dashboard_hidden_tags}
    records = [record for record in records if compact_upper(record.get("mac", "")) not in hidden_tags]
    names = {}
    for key, name in settings.tag_names.items():
        names.setdefault(compact_upper(key), name)

    history_hours = _history_hours(history_hours)
    now_utc = datetime.now(timezone.utc)
    since = now_utc - timedelta(hours=history_hours)
    summaries = await storage.get_weather_summaries(
        [record["mac"] for record in records if record.get("mac") is not None],
        since.isoformat(), now_utc.isoformat(),
        gap_seconds=2 * (settings.scan_interval_seconds + settings.scan_timeout_seconds),
    )

    cards = []
    for record in records:
        mac = record.get("mac")
        if mac is None:
            continue

        summary = summaries[mac].copy()
        trend = summary.pop("trend")
        summary["trend_path"] = _trend_path(
            trend, since, now_utc, summary["temperature_low"], summary["temperature_high"],
        )

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
    history_hours = _history_hours(request.query_params.get("hours"))
    cards = await _build_dashboard_cards(storage, settings, history_hours)
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
            "history_hours": history_hours,
            "history_windows": HISTORY_HOURS,
            "using_default_credentials": settings.is_using_default_credentials(),
        },
    )


@router.get("/api/current")
async def api_current(request: Request):
    storage: Storage = request.app.state.storage
    settings_store: SettingsStore = request.app.state.settings_store
    settings = await settings_store.get()
    history_hours = _history_hours(request.query_params.get("hours"))
    response = {
        "cards": await _build_dashboard_cards(storage, settings, history_hours),
        "dashboard_options": {
            "visible_metrics": settings.dashboard_visible_metrics,
            "history_hours": history_hours,
            "refresh_seconds": settings.dashboard_refresh_seconds,
        },
    }
    if settings.dashboard_show_storage_info:
        response["storage"] = await storage.get_storage_stats()
    return response
