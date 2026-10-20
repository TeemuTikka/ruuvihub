"""Shared datetime display formats for file configuration and web templates."""

from datetime import datetime, timezone

DEFAULT_DATETIME_FORMAT = "fi"
DATETIME_FORMATS = ("fi", "iso")


def format_timestamp(value, display_format=DEFAULT_DATETIME_FORMAT):
    """Render a timestamp in server local time; preserve invalid input as text."""
    if not value:
        return "—"
    try:
        timestamp = datetime.fromisoformat(value)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        pattern = "%Y-%m-%d %H:%M:%S" if display_format == "iso" else "%d.%m.%Y %H:%M:%S"
        return timestamp.astimezone().strftime(pattern)
    except (TypeError, ValueError):
        return str(value)
