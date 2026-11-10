"""Supported sensor-card fields and comparison bounds."""

DASHBOARD_METRICS = (
    ("temperature", "Temperature"),
    ("temperature_range", "Temperature low / high"),
    ("temperature_trend", "Temperature trend"),
    ("humidity", "Humidity"),
    ("pressure", "Pressure"),
    ("battery", "Battery"),
)
DEFAULT_DASHBOARD_METRICS = [key for key, _ in DASHBOARD_METRICS]
MAX_DASHBOARD_CHANGE_HOURS = 168

HISTORY_HOURS = (6, 12, 24, 48, 72)
DEFAULT_HISTORY_HOURS = 24
LEGACY_METRICS = {
    "temperature_change": "temperature_trend",
    "day_temperature_range": "temperature_range",
    "night_temperature_range": "temperature_range",
}
