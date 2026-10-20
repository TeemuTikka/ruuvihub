"""Supported sensor-card fields and comparison bounds."""

DASHBOARD_METRICS = (
    ("temperature", "Temperature"),
    ("temperature_change", "Temperature change"),
    ("day_temperature_range", "Day low / high"),
    ("night_temperature_range", "Night low / high"),
    ("humidity", "Humidity"),
    ("pressure", "Pressure"),
    ("battery", "Battery"),
)
DEFAULT_DASHBOARD_METRICS = [key for key, _ in DASHBOARD_METRICS]
MAX_DASHBOARD_CHANGE_HOURS = 168
