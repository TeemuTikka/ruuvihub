"""Background checks for RuuviHub release notifications (no installation)."""

import asyncio
from datetime import datetime, timezone
import re

import requests

_LATEST_URL = "https://api.github.com/repos/TeemuTikka/ruuvihub/releases/latest"
_NOTES_URL = "https://github.com/TeemuTikka/ruuvihub/releases/tag/"
_STABLE_TAG = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
CHECK_INTERVAL_SECONDS = 24 * 60 * 60


def _version_parts(version: object) -> tuple[int, ...] | None:
    if not isinstance(version, str) or len(version) > 64:
        return None
    match = _STABLE_TAG.fullmatch(version)
    return tuple(map(int, match.groups())) if match else None


def fetch_latest_version() -> str | None:
    response = requests.get(
        _LATEST_URL,
        headers={"Accept": "application/vnd.github+json"},
        timeout=(3, 5),
        allow_redirects=False,
    )
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise ValueError("GitHub release check failed")
    release = response.json()
    if not isinstance(release, dict):
        raise ValueError("unexpected GitHub release response")
    if release.get("draft") is True or release.get("prerelease") is True:
        return None
    version = release.get("tag_name")
    if _version_parts(version) is None:
        raise ValueError("GitHub release did not contain a stable RuuviHub tag")
    return version


class ReleaseCheck:
    def __init__(self, current_version: str):
        self.current_version = current_version
        self.latest_version: str | None = None
        self.last_checked_at: str | None = None
        self.failed = False
        self._task: asyncio.Task | None = None

    @property
    def checking(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def release_notes(self) -> str | None:
        return _NOTES_URL + self.latest_version if self.latest_version else None

    @property
    def update_available(self) -> bool:
        current = _version_parts(self.current_version)
        latest = _version_parts(self.latest_version)
        return current is not None and latest is not None and latest > current

    def start_check(self) -> asyncio.Task:
        if not self.checking:
            self._task = asyncio.create_task(self._check())
        return self._task

    async def _check(self) -> None:
        try:
            self.latest_version = await asyncio.to_thread(fetch_latest_version)
        except (requests.RequestException, ValueError):
            self.failed = True
        else:
            self.failed = False
        self.last_checked_at = datetime.now(timezone.utc).isoformat()

    async def run_periodically(self) -> None:
        while True:
            self.start_check()
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
