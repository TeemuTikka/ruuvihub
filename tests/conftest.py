"""Keep automatic release checks off the network during tests."""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import release_check


@pytest.fixture(autouse=True)
def stub_release_check(monkeypatch):
    monkeypatch.setattr(release_check, "fetch_latest_version", lambda: None)
