"""Bounded HTTP response reads (audit H2).

A shared cap so a runaway, slow, or hostile upstream response can't be read
fully into memory and exhaust the process. stdlib-only — the fetchers that use
this stay zero-dependency.
"""

from __future__ import annotations

# Comfortably above the largest legitimate payload we fetch (SEC companyfacts
# JSON for mega-caps runs ~15 MB), far below anything that threatens the process.
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
# Filing HTML is handed to a regex stripper, so bound it tighter: a 10-K is a few
# MB, and a smaller cap also limits worst-case stripping work.
MAX_FILING_BYTES = 10 * 1024 * 1024


def read_capped(resp: object, max_bytes: int = MAX_RESPONSE_BYTES) -> bytes:
    """Read at most ``max_bytes`` from a urllib response; raise if it overflows.

    Reads one byte past the cap so an exactly-at-limit body still succeeds while
    anything larger is rejected instead of streamed into memory.
    """
    data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"response exceeds {max_bytes} bytes (runaway or hostile upstream?)")
    return data
