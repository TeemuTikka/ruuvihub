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

HISTORY_WINDOWS = {
    1: ("1h", "1 hour"),
    3: ("3h", "3 hours"),
    6: ("6h", "6 hours"),
    12: ("12h", "12 hours"),
    24: ("1d", "1 day"),
    48: ("2d", "2 days"),
    72: ("3d", "3 days"),
    120: ("5d", "5 days"),
    168: ("7d", "7 days"),
}
DEFAULT_HISTORY_HOURS = 24
LEGACY_METRICS = {
    "temperature_change": "temperature_trend",
    "day_temperature_range": "temperature_range",
    "night_temperature_range": "temperature_range",
}
