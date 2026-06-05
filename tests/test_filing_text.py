"""Tests for filing-text extraction (slice 48 / B12)."""

from __future__ import annotations

import time

from traders.filing_text import extract_item, extract_text


def test_extract_text_strips_tags_scripts_and_entities():
    html = (
        "<html><head><style>.x{color:red}</style></head>"
        "<body><script>var a=1;</script><p>Revenue rose 10&amp;more.</p>"
        "<div>Margins   held.</div></body></html>"
    )
    text = extract_text(html)
    assert "color:red" not in text and "var a" not in text  # script/style dropped
    assert "Revenue rose 10&more." in text  # entity decoded
    assert "Margins held." in text  # whitespace collapsed


def test_extract_text_empty():
    assert extract_text("") == ""
    assert extract_text(None) == ""


def test_extract_text_linear_on_pathological_unclosed_scripts():
    # Many unclosed <script> opens backtracked quadratically in the old regex
    # (audit H2). The linear stripper handles them in well under a second.
    raw = "<script>" * 40000
    start = time.perf_counter()
    text = extract_text(raw)
    assert time.perf_counter() - start < 2.0
    assert "script" not in text.lower()


def test_extract_item_returns_section_body_not_toc():
    text = (
        "Table of Contents Item 1A. Risk Factors 12 Item 7. MD&A 30 "  # the TOC
        "Item 1A. Risk Factors Our business faces supply-chain risk and FX risk. "
        "Item 1B. Unresolved Staff Comments None."
    )
    excerpt = extract_item(text, "Item 1A. Risk Factors")
    assert excerpt is not None
    assert excerpt.startswith("Item 1A. Risk Factors Our business faces supply-chain")
    assert "Unresolved Staff Comments" not in excerpt  # stopped at the next Item


def test_extract_item_tries_labels_in_order():
    text = "Item 7. Management's Discussion and Analysis Liquidity remained strong. Item 8."
    assert extract_item(text, "Item 1A. Risk Factors", "Item 7.") is not None


def test_extract_item_caps_length():
    text = "Item 1A. Risk Factors " + "x" * 5000
    assert len(extract_item(text, "Item 1A. Risk Factors", max_chars=500)) == 500


def test_extract_item_missing_returns_none():
    assert extract_item("nothing relevant here", "Item 1A. Risk Factors") is None
    assert extract_item("", "Item 1A.") is None
