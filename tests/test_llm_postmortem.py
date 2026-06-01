"""Tests for the LLM-backed post-mortem generator (slice 28).

Hermetic: every test injects a fake client, so the suite never imports
``anthropic`` or touches the network.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from traders.llm_postmortem import LLMPostMortemGenerator
from traders.post_mortems import ClosedPosition, ThesisContext

_POS = ClosedPosition(
    position_id=1,
    ticker="AAA",
    thesis_id=1,
    direction="long",
    opened_at="2026-01-01",
    closed_at="2026-02-01",
    entry_price=100.0,
    exit_price=120.0,
    size_pct=3.0,
)
_THESIS = ThesisContext(
    thesis_id=1,
    thesis_type="value",
    direction="long",
    conviction=4,
    suggested_size_pct=3.0,
    exit_condition="re-rate to peers",
    rationale="cheap on cash flow",
)
_VALID = {
    "outcome": "AAA worked: +20% as the value thesis expected.",
    "lessons": "Let winners run; the exit was a touch early.",
}


def _client(tool_input=None, recorder=None, raises=None):
    def create(**kwargs):
        if recorder is not None:
            recorder.append(kwargs)
        if raises is not None:
            raise raises
        block = SimpleNamespace(type="tool_use", input=tool_input)
        return SimpleNamespace(content=[block])

    return SimpleNamespace(messages=SimpleNamespace(create=create))


def test_valid_output_maps_to_draft():
    d = LLMPostMortemGenerator(client=_client(_VALID)).generate(_POS, _THESIS)
    assert d.outcome == "AAA worked: +20% as the value thesis expected."
    assert d.lessons == "[llm] Let winners run; the exit was a touch early."


def test_api_error_falls_back_deterministically():
    d = LLMPostMortemGenerator(client=_client(raises=RuntimeError("network down"))).generate(
        _POS, _THESIS
    )
    assert "+20.00%" in d.outcome  # deterministic PnL line
    assert d.lessons.startswith("[llm]")
    assert "unavailable" in d.lessons.lower()


def test_empty_fields_fall_back():
    d = LLMPostMortemGenerator(client=_client({"outcome": "  ", "lessons": ""})).generate(
        _POS, _THESIS
    )
    assert "unavailable" in d.lessons.lower()


def test_missing_prices_fallback_is_graceful():
    pos = replace(_POS, entry_price=None, exit_price=None)
    d = LLMPostMortemGenerator(client=_client(raises=RuntimeError("x"))).generate(pos, _THESIS)
    assert "unknown" in d.outcome.lower()  # no crash on missing prices


def test_request_forces_tool_caches_and_delimits():
    rec: list[dict] = []
    LLMPostMortemGenerator(client=_client(_VALID, recorder=rec)).generate(_POS, _THESIS)
    kw = rec[0]
    assert kw["tool_choice"] == {"type": "tool", "name": "record_post_mortem"}
    assert kw["tools"][0]["name"] == "record_post_mortem"
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "untrusted" in kw["system"][0]["text"].lower()
    user = kw["messages"][0]["content"]
    assert "+20.00%" in user  # PnL handed to the model as a fact
    assert "<thesis_text>" in user and "cheap on cash flow" in user  # rationale as data


def test_model_override():
    rec: list[dict] = []
    LLMPostMortemGenerator(
        client=_client(_VALID, recorder=rec), model="claude-haiku-4-5-20251001"
    ).generate(_POS, _THESIS)
    assert rec[0]["model"] == "claude-haiku-4-5-20251001"
