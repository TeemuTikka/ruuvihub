"""Tests for src/mac_utils.py -- MAC address format reconciliation helpers."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mac_utils import canonicalize_mac, compact_upper, resolve_known_mac


def test_compact_upper_strips_separators_and_uppercases():
    assert compact_upper("cf:b5:0a:f6:7c:e4") == "CFB50AF67CE4"
    assert compact_upper("CF-B5-0A-F6-7C-E4") == "CFB50AF67CE4"
    assert compact_upper("cfb50af67ce4") == "CFB50AF67CE4"


def test_compact_upper_handles_none_and_blank():
    assert compact_upper(None) == ""
    assert compact_upper("") == ""


def test_canonicalize_mac_converts_12_hex_chars_to_colon_form():
    assert canonicalize_mac("cfb50af67ce4") == "CF:B5:0A:F6:7C:E4"
    assert canonicalize_mac("CF-B5-0A-F6-7C-E4") == "CF:B5:0A:F6:7C:E4"


def test_canonicalize_mac_leaves_non_12_hex_input_unchanged():
    assert canonicalize_mac("not-a-mac") == "not-a-mac"
    assert canonicalize_mac("  padded  ") == "padded"


def test_resolve_known_mac_matches_regardless_of_format():
    known = {"cfb50af67ce4"}
    assert resolve_known_mac("CF:B5:0A:F6:7C:E4", known) == "cfb50af67ce4"
    assert resolve_known_mac("cf-b5-0a-f6-7c-e4", known) == "cfb50af67ce4"


def test_resolve_known_mac_falls_back_to_canonical_when_unseen():
    assert resolve_known_mac("cfb50af67ce4", set()) == "CF:B5:0A:F6:7C:E4"


def test_resolve_known_mac_prefers_first_matching_known_key():
    known = ["CF:B5:0A:F6:7C:E4"]
    assert resolve_known_mac("cfb50af67ce4", known) == "CF:B5:0A:F6:7C:E4"
