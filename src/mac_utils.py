"""Shared helpers for reconciling MAC address string formats.

RuuviTag MACs enter the system in more than one textual shape. Some Pi
setups' ``ruuvitag_sensor`` builds report a compact lowercase form with no
separators (e.g. ``cfb50af67ce4``); a user typing a MAC into a URL, or
copying it from documentation, more often uses the colon-separated
uppercase form (e.g. ``CF:B5:0A:F6:7C:E4``). ``Storage`` and
``Settings.tag_names`` both key their data by whatever exact string was
used the *first* time a given tag was seen/named -- so looking one up
with a *different* textual shape of the very same physical address
silently misses, even though it is "the same" MAC.

These helpers let call sites resolve a possibly-differently-formatted MAC
back to whichever exact string is already used as a key in storage or
settings, before falling back to a canonical (uppercase, colon-separated)
form for a MAC that has never been seen in any format yet (e.g. one a
user typed manually into the sensor page's URL).
"""

import re
from typing import Iterable


def compact_upper(mac: str) -> str:
    """Reduce ``mac`` to its bare hex digits, uppercased.

    Used purely as a comparison key -- two MAC strings that differ only
    in case or separator style (``:``, ``-``, or none) compare equal via
    this function.
    """
    return re.sub(r"[^0-9A-Fa-f]", "", mac or "").upper()


def canonicalize_mac(mac: str) -> str:
    """Return the canonical uppercase colon-separated form of ``mac``.

    Only converts strings that are exactly 12 hex characters once
    separators are stripped; anything else (blank, unexpected length,
    garbage) is returned trimmed but otherwise unchanged, so a mistyped
    value fails an exact-match lookup instead of being silently mangled
    into something else.
    """
    raw = (mac or "").strip()
    compact = compact_upper(raw)
    if len(compact) == 12:
        return ":".join(compact[i : i + 2] for i in range(0, 12, 2))
    return raw


def resolve_known_mac(mac: str, known_macs: Iterable[str]) -> str:
    """Resolve ``mac`` to the exact string used as a key in ``known_macs``.

    Compares case/separator-insensitively (via :func:`compact_upper`)
    against every candidate in ``known_macs`` (typically
    ``Storage.known_macs()`` unioned with ``Settings.tag_names`` keys)
    and returns the first match found, preserving whatever original
    format that candidate is actually stored under.

    Falls back to :func:`canonicalize_mac` when no candidate matches --
    i.e. a MAC that has genuinely never been seen in any format, most
    likely one typed manually into a URL for a tag that hasn't reported
    yet.
    """
    target = compact_upper(mac)
    if target:
        for candidate in known_macs:
            if compact_upper(candidate) == target:
                return candidate
    return canonicalize_mac(mac)
