"""Per-sensor history page, JSON history API, and CSV export."""

import csv
import io
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from mac_utils import resolve_known_mac
from settings import Settings
from storage import Storage

router = APIRouter()

DEFAULT_RANGE = "24h"
VALID_RANGES = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "all": None,
}
RANGE_OPTIONS = [
    ("1h", "1h"),
    ("24h", "24h"),
    ("7d", "7d"),
    ("30d", "30d"),
    ("all", "All"),
]
CSV_FIELDS = ["timestamp", "temperature", "humidity", "pressure", "battery"]


def _normalize_range(range_value: str | None) -> str:
    if range_value in VALID_RANGES:
        return range_value
    return DEFAULT_RANGE


def _since_for_range(range_value: str) -> str | None:
    delta = VALID_RANGES[_normalize_range(range_value)]
    if delta is None:
        return None
    return (datetime.now(timezone.utc) - delta).isoformat()


def _csv_content(records: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_FIELDS)
    for record in records:
        writer.writerow([record.get(field, "") for field in CSV_FIELDS])
    return buffer.getvalue()


def _download_filename(mac: str, range_value: str) -> str:
    safe_mac = re.sub(r"[^A-Za-z0-9_.-]+", "-", mac).strip("-") or "sensor"
    return f"{safe_mac}_{range_value}.csv"


async def _resolve_mac(request: Request, mac: str) -> tuple[str, Settings]:
    """Resolve a URL-supplied MAC to the exact key ``Storage`` uses.

    A URL's ``mac`` segment may be typed/copied in any of the accepted
    manual formats (``E8:CB:E9:29:12:49``, ``e8cbe9291249``,
    ``E8-CB-E9-29-12-49``, ...), while ``Storage`` keys its data by
    whichever exact string the scanner actually used -- which, on some Pi
    setups, is a compact lowercase form.

    This resolves *only* against ``Storage.known_macs()`` so history/CSV
    lookups (which query storage) are deterministic. Deliberately does
    **not** also fold in ``Settings.tag_names`` keys here: on an install
    where a name was saved under a different textual shape than storage
    uses for the same physical tag (the exact legacy mismatch this PR
    fixes), merging both key sets into one lookup makes the *result*
    depend on iteration order -- sometimes returning the settings key
    (so the history/CSV query against storage then finds nothing) and
    sometimes the storage key (so an exact ``tag_names.get()`` then
    misses the name). Resolving the friendly name is therefore always
    done independently, via ``_friendly_name()``, against
    ``settings.tag_names`` alone.
    """
    storage: Storage = request.app.state.storage
    settings = await request.app.state.settings_store.get()
    resolved = resolve_known_mac(mac, await storage.known_macs())
    return resolved, settings


def _friendly_name(mac: str, settings: Settings) -> str:
    """Look up ``mac``'s friendly name, tolerating format mismatches.

    Resolved independently from the storage-keyed ``mac`` returned by
    ``_resolve_mac`` -- ``settings.tag_names`` may key the same tag under
    a different textual shape (see ``_resolve_mac``'s docstring), so this
    performs its own case/separator-insensitive match against
    ``tag_names`` rather than assuming the two sources share one key.
    """
    resolved = resolve_known_mac(mac, settings.tag_names.keys())
    return settings.tag_names.get(resolved, mac)


@router.get("/sensor/{mac}")
async def sensor_page(
    request: Request,
    mac: str,
):
    mac, settings = await _resolve_mac(request, mac)
    range_value = _normalize_range(request.query_params.get("range"))
    return request.app.state.templates.TemplateResponse(
        request,
        "sensor.html",
        {
            "mac": mac,
            "friendly_name": _friendly_name(mac, settings),
            "range": range_value,
            "range_options": RANGE_OPTIONS,
            "using_default_credentials": settings.is_using_default_credentials(),
        },
    )


@router.get("/api/history/{mac}")
async def history_api(
    request: Request,
    mac: str,
    range_value: str = Query(DEFAULT_RANGE, alias="range"),
    chart: bool = Query(False),
):
    """Return chart-ready history records, lightly sampled for long ranges."""
    mac, settings = await _resolve_mac(request, mac)
    storage: Storage = request.app.state.storage
    normalized_range = _normalize_range(range_value)
    now = datetime.now(timezone.utc)
    until = now.isoformat()
    delta = VALID_RANGES[normalized_range]
    since = (now - delta).isoformat() if delta is not None else None
    if chart:
        records = await storage.get_chart_history(
            mac, since, until,
            gap_seconds=2 * (settings.scan_interval_seconds + settings.scan_timeout_seconds),
        )
        return {
            "records": records,
            "since": since or (records[0]["timestamp"] if records else (now - timedelta(hours=24)).isoformat()),
            "until": until,
        }
    if normalized_range in {"1h", "24h"}:
        # Short ranges rarely accumulate enough rows to need sampling, and
        # keeping full resolution here matters most for fine-grained charts.
        return await storage.get_history(mac, since=since, until=until)
    # Longer ranges are downsampled directly in SQL (bounded selection/
    # memory) rather than fetching the full history and discarding most
    # of it in Python.
    return await storage.get_history_sampled(mac, since=since, until=until, max_points=500)


@router.get("/api/history/{mac}/export")
async def export_history_csv(
    request: Request,
    mac: str,
    range_value: str = Query(DEFAULT_RANGE, alias="range"),
):
    mac, _settings = await _resolve_mac(request, mac)
    normalized_range = _normalize_range(range_value)
    storage: Storage = request.app.state.storage
    records = await storage.get_history(
        mac, since=_since_for_range(normalized_range),
        until=datetime.now(timezone.utc).isoformat(),
    )
    filename = _download_filename(mac, normalized_range)
    return Response(
        content=_csv_content(records),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

