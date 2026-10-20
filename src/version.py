"""Running-version helper for RuuviHub.

Release archives built by CI (see ``.github/workflows/release.yml``)
ship a ``VERSION`` file at the project root containing the git tag
(e.g. ``v1.2.3``). This module reads it once so the value can be
exposed via ``/healthz`` and the web UI footer — handy for confirming
which build any given Raspberry Pi is running, especially with
multiple independent installs.

Falls back to ``"dev"`` when no ``VERSION`` file is present (e.g. when
running straight from a git checkout rather than an installed release
archive).
"""

from pathlib import Path

_VERSION_FILE = Path(__file__).parent.parent / "VERSION"
_FALLBACK_VERSION = "dev"

_cached_version: str | None = None


def get_version() -> str:
    """Return the running version string, cached after the first read."""
    global _cached_version
    if _cached_version is None:
        try:
            _cached_version = _VERSION_FILE.read_text(encoding="utf-8").strip() or _FALLBACK_VERSION
        except OSError:
            _cached_version = _FALLBACK_VERSION
    return _cached_version
