"""Tests for src/version.py – the packaged-VERSION-file reader.

test_web_app.py's healthz test only exercises the "dev" fallback (no
VERSION file present). This file covers the production path used by
release archives: reading a real, non-empty VERSION file, plus the
empty-file and unreadable-file fallback cases. version._cached_version
is reset before/after each test so tests don't leak state into each
other or into other test modules importing the same module.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

import version as version_module
from version import get_version


@pytest.fixture(autouse=True)
def reset_version_cache():
    version_module._cached_version = None
    yield
    version_module._cached_version = None


def test_get_version_reads_packaged_version_file(tmp_path, monkeypatch):
    version_file = tmp_path / "VERSION"
    version_file.write_text("v1.2.3\n", encoding="utf-8")
    monkeypatch.setattr(version_module, "_VERSION_FILE", version_file)

    assert get_version() == "v1.2.3"


def test_get_version_caches_after_first_read(tmp_path, monkeypatch):
    version_file = tmp_path / "VERSION"
    version_file.write_text("v1.2.3", encoding="utf-8")
    monkeypatch.setattr(version_module, "_VERSION_FILE", version_file)

    assert get_version() == "v1.2.3"

    # Changing the file after the first read must not affect the
    # already-cached value.
    version_file.write_text("v9.9.9", encoding="utf-8")
    assert get_version() == "v1.2.3"


def test_get_version_falls_back_to_dev_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(version_module, "_VERSION_FILE", tmp_path / "does-not-exist" / "VERSION")

    assert get_version() == "dev"


def test_get_version_falls_back_to_dev_when_file_empty(tmp_path, monkeypatch):
    version_file = tmp_path / "VERSION"
    version_file.write_text("   \n", encoding="utf-8")
    monkeypatch.setattr(version_module, "_VERSION_FILE", version_file)

    assert get_version() == "dev"
