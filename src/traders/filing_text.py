"""Filing text extraction — turn a filing's HTML into a readable excerpt.

Backlog item B12: the Researcher's filing DataPoints carry only a title ("AAPL
filed 10-K on …"); the downstream note (and the LLM thesis that reads it) has no
substance. These pure helpers strip a filing document to plain text and best-effort
extract a named section (Risk Factors, MD&A), so the Researcher can include a real
excerpt instead of just a headline.

Pure stdlib (``re`` + ``html``), no I/O — the network fetch lives in the EDGAR data
source. 10-K HTML is messy and varies by filer, so extraction is best-effort: it
prefers the *last* occurrence of a section heading (the body, not the table of
contents) and stops at the next ``Item N`` boundary or a length cap.
"""

from __future__ import annotations

import html as _html
import re

_SCRIPT_STYLE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_ITEM_BOUNDARY = re.compile(r"\bItem\s+\d+[A-Za-z]?\b")

DEFAULT_MAX_CHARS = 2000


def extract_text(raw: str | None) -> str:
    """Strip HTML to collapsed plain text (scripts/styles/tags removed, entities decoded)."""
    if not raw:
        return ""
    without_code = _SCRIPT_STYLE.sub(" ", raw)
    detagged = _TAG.sub(" ", without_code)
    return _WS.sub(" ", _html.unescape(detagged)).strip()


def extract_item(text: str, *labels: str, max_chars: int = DEFAULT_MAX_CHARS) -> str | None:
    """Best-effort excerpt of the first matching ``labels`` section, or None.

    Uses the *last* occurrence of a label (the body usually follows the table of
    contents) and runs to the next ``Item N`` heading or ``max_chars``.
    """
    if not text:
        return None
    low = text.lower()
    for label in labels:
        idx = low.rfind(label.lower())
        if idx == -1:
            continue
        rest = text[idx:]
        boundary = _ITEM_BOUNDARY.search(rest, len(label) + 1)
        excerpt = rest[: boundary.start() if boundary else len(rest)].strip()
        return excerpt[:max_chars].strip()
    return None
