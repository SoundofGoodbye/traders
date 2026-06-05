"""Unit tests for the plain-English presentation layer.

`traders.web.explain` is pure (no FastAPI, no DB), so these run in the default
hermetic suite without the `web` extra.
"""

from traders.web.explain import (
    describe_conviction,
    describe_job_status,
    describe_position,
    describe_schedule,
    explain_post_mortem,
    explain_thesis,
    parse_research_note,
    strip_tag,
    thesis_headline,
)
from traders.web.queries import PostMortem, Thesis


def _thesis(
    *,
    ticker="AAA",
    thesis_type="momentum",
    direction="long",
    conviction=5,
    size=2.0,
    exit_condition="Exit on 12-1 momentum turning negative or a -8% stop.",
    rationale="[signal] 12-1 momentum +103.7% (RSI 52.9); trend-continuation long.",
) -> Thesis:
    return Thesis(
        id=1,
        ticker=ticker,
        thesis_type=thesis_type,
        direction=direction,
        conviction=conviction,
        suggested_size_pct=size,
        exit_condition=exit_condition,
        rationale=rationale,
        created_at="2026-06-01T00:00:00+00:00",
        status="proposed",
        run_id=1,
        research_run_id=1,
    )


# --- tag stripping ----------------------------------------------------------


def test_strip_tag_removes_source_prefixes():
    assert strip_tag("[signal] 12-1 momentum +5.0%").startswith("12-1 momentum")
    assert strip_tag("[llm] cheap on FCF") == "cheap on FCF"
    assert strip_tag("[stub] Baseline mean-reversion thesis") == "Baseline mean-reversion thesis"
    assert strip_tag(None) == ""


# --- momentum ---------------------------------------------------------------


def test_momentum_strategy_is_plain_and_keeps_the_number():
    ex = explain_thesis(_thesis())
    assert "103.7%" in ex.strategy
    assert "past year" in ex.strategy
    assert "buy" in ex.strategy and "uptrend" in ex.strategy
    # RSI 52.9 is mid-range -> described as neutral, with the reading shown.
    assert "52.9" in ex.strategy
    assert "neither overbought nor oversold" in ex.strategy


def test_momentum_sell_when_translates_trigger_and_stop():
    ex = explain_thesis(_thesis())
    assert ex.sell_when == (
        "Sell if the year-long uptrend fades (its 12-1 momentum turns negative), "
        "or you're down 8% from your buy price (a safety stop to cap the loss)."
    )


def test_momentum_glossary_has_the_terms_used():
    terms = {g.term for g in explain_thesis(_thesis()).glossary}
    assert {"12-1 momentum", "RSI", "Stop-loss"} <= terms
    # Unrelated value terms are not dragged in.
    assert "FCF yield" not in terms


def test_overbought_rsi_is_flagged():
    ex = explain_thesis(
        _thesis(rationale="[signal] 12-1 momentum +30.0% (RSI 81.0); trend-continuation long.")
    )
    assert "overbought" in ex.strategy


# --- mean-reversion ---------------------------------------------------------


def test_mean_reversion_strategy_and_exit():
    ex = explain_thesis(
        _thesis(
            thesis_type="mean-reversion",
            rationale="[signal] oversold: 20-day z -2.3, RSI 18.0; mean-reversion bounce long.",
            exit_condition="Exit when price reverts to its 20-day mean (z>=0) or a -8% stop.",
        )
    )
    assert "beaten-down" in ex.strategy and "bounce" in ex.strategy
    assert "-2.3" in ex.strategy and "18.0/100" in ex.strategy
    assert "20-day average" in ex.sell_when and "8%" in ex.sell_when
    terms = {g.term for g in ex.glossary}
    assert {"20-day z-score", "Mean-reversion", "RSI", "Stop-loss"} <= terms


# --- value ------------------------------------------------------------------


def test_value_strategy_and_glossary():
    ex = explain_thesis(
        _thesis(
            thesis_type="value",
            conviction=4,
            rationale="[signal] cheap: E/P 8.0%, B/P 0.7, FCF yield 6.0% (3/3 value flags); "
            "value long.",
            exit_condition="Exit when the valuation re-rates (cheap flags lapse) or a -8% stop.",
        )
    )
    assert "cheap" in ex.strategy and "3 of 3 value yardsticks" in ex.strategy
    assert "stops looking cheap" in ex.sell_when
    terms = {g.term for g in ex.glossary}
    assert {"Earnings yield (E/P)", "Book-to-price (B/P)", "FCF yield", "Value flags"} <= terms


def test_value_strategy_surfaces_quality_score():
    ex = explain_thesis(
        _thesis(
            thesis_type="value",
            conviction=5,
            rationale="[signal] cheap: E/P 8.0%, B/P 0.7, FCF yield 6.0% (3/3 value flags); "
            "value long. Quality: Piotroski 8/9 (9 tests).",
            exit_condition="Exit when the valuation re-rates (cheap flags lapse) or a -8% stop.",
        )
    )
    assert "8 out of 9" in ex.strategy and "healthier" in ex.strategy
    assert "Piotroski score" in {g.term for g in ex.glossary}


def test_value_strategy_surfaces_margin_of_safety():
    ex = explain_thesis(
        _thesis(
            thesis_type="value",
            conviction=4,
            rationale="[signal] cheap: E/P 8.0%, B/P 0.7, FCF yield 6.0% (3/3 value flags); "
            "value long. Intrinsic value ~$140/sh; margin of safety 29% (5yr owner "
            "earnings; r 10%, g 2%); price implies 1% growth.",
            exit_condition="Exit when the valuation re-rates (cheap flags lapse) or a -8% stop.",
        )
    )
    assert "$140 a share" in ex.strategy and "29%" in ex.strategy
    terms = {g.term for g in ex.glossary}
    assert {"Intrinsic value", "Margin of safety", "Owner earnings"} <= terms


# --- earnings warning -------------------------------------------------------


def test_earnings_warning_surfaces_when_present():
    ex = explain_thesis(
        _thesis(
            rationale="[signal] 12-1 momentum +20.0% (RSI 55.0); trend-continuation long. "
            "Earnings in 3d — event risk."
        )
    )
    assert ex.earnings_warning is not None
    assert "earnings" in ex.earnings_warning.lower() and "3 days" in ex.earnings_warning


def test_no_earnings_warning_by_default():
    assert explain_thesis(_thesis()).earnings_warning is None


# --- snapshot caveat (B8) ---------------------------------------------------


def test_value_thesis_carries_snapshot_caveat():
    ex = explain_thesis(
        _thesis(
            thesis_type="value",
            rationale="[signal] cheap: E/P 8.0%, B/P 0.7, FCF yield 6.0% (3/3 value flags); "
            "value long.",
        )
    )
    assert ex.caveat is not None
    assert "snapshot" in ex.caveat and "filings" in ex.caveat


def test_non_value_thesis_has_no_caveat():
    # A momentum thesis makes no snapshot-based claim -> no caveat.
    assert explain_thesis(_thesis()).caveat is None


# --- LLM / custom theses degrade gracefully ---------------------------------


def test_llm_thesis_uses_generic_strategy_and_verbatim_exit():
    ex = explain_thesis(
        _thesis(
            thesis_type="value",
            rationale="[llm] Cheap on FCF with improving margins.",
            exit_condition="Re-rate to peers, or a -8% stop.",
        )
    )
    # No parseable signal numbers -> generic, still names the bet and the action.
    assert "value idea" in ex.strategy and "buy AAA" in ex.strategy
    # Custom exit shown in full (not reduced to just the stop), tag stripped.
    assert ex.sell_when == "Sell when: Re-rate to peers, or a -8% stop."


def test_missing_exit_is_handled():
    ex = explain_thesis(_thesis(exit_condition=None))
    assert "re-evaluate" in ex.sell_when.lower()


# --- thesis-list labels -----------------------------------------------------


def test_thesis_headline_per_type_and_direction():
    assert thesis_headline(_thesis(thesis_type="momentum")) == "Buy AAA to ride its uptrend"
    assert (
        thesis_headline(_thesis(thesis_type="mean-reversion"))
        == "Buy AAA expecting a bounce back up"
    )
    assert thesis_headline(_thesis(thesis_type="value")) == "Buy AAA while it still looks cheap"
    # Direction flips the verb; an unknown type degrades to a bare action.
    assert thesis_headline(_thesis(thesis_type="momentum", direction="short")).startswith(
        "Short-sell AAA"
    )
    assert thesis_headline(_thesis(thesis_type="catalyst")) == "Buy AAA"


def test_describe_conviction_maps_score_to_word():
    assert describe_conviction(5) == "very high"
    assert describe_conviction(3) == "medium"
    assert describe_conviction(1) == "very low"
    assert describe_conviction(0) == "—"  # out of range -> no claim


def test_describe_position_uses_past_tense():
    # A position already exists, so it reads as what you did, not an instruction.
    assert describe_position("long") == "Bought"
    assert describe_position("short") == "Short-sold"
    assert describe_position(None) == "—"


# --- post-mortems (reviews) -------------------------------------------------


def _post_mortem(
    *,
    direction="long",
    thesis_type="momentum",
    entry_price=100.0,
    exit_price=120.0,
    opened_at="2026-05-01T00:00:00+00:00",
    closed_at="2026-05-15T00:00:00+00:00",
    outcome="[stub] AAA long: +20.00% (win).",
    lessons="[stub] momentum thesis closed as win.",
) -> PostMortem:
    return PostMortem(
        id=1,
        reviewer_run_id=1,
        position_id=1,
        ticker="AAA",
        direction=direction,
        thesis_type=thesis_type,
        entry_price=entry_price,
        exit_price=exit_price,
        size_pct=2.0,
        opened_at=opened_at,
        closed_at=closed_at,
        outcome=outcome,
        lessons=lessons,
        created_at="2026-05-16T00:00:00+00:00",
    )


def test_post_mortem_long_win_reads_plainly():
    ex = explain_post_mortem(_post_mortem())
    assert ex.result == "gain"
    assert ex.summary == (
        "A trend-following buy on AAA. Bought at 100.00, sold at 120.00 — "
        "a +20.0% gain over 14 days."
    )
    # Reviewer's own lines are kept but the machine source tag is stripped.
    assert ex.outcome.startswith("AAA long") and "[stub]" not in ex.outcome
    assert "[stub]" not in ex.lessons


def test_post_mortem_short_loss_uses_cover_language():
    # short entry 100 -> exit 120 is a loss; the trade verbs flip to short/cover.
    ex = explain_post_mortem(_post_mortem(direction="short"))
    assert ex.result == "loss"
    assert "Short-sold at 100.00, covered at 120.00" in ex.summary
    assert "-20.0% loss" in ex.summary


def test_post_mortem_missing_price_is_honest():
    ex = explain_post_mortem(_post_mortem(exit_price=None))
    assert ex.result == "unknown"
    assert "isn't clear" in ex.summary


# --- scheduled jobs ---------------------------------------------------------


def test_describe_schedule_known_crons():
    assert describe_schedule("0 8 * * 1-5") == "Every weekday at 08:00"
    assert describe_schedule("0 9 * * 6") == "Every Saturday at 09:00"
    assert describe_schedule("30 6 * * *") == "Every day at 06:30"


def test_describe_schedule_falls_back_to_raw_when_unsure():
    # A day-of-month constraint isn't something we translate -> show it verbatim.
    assert describe_schedule("0 8 1 * *") == "0 8 1 * *"
    assert describe_schedule("not a cron") == "not a cron"
    assert describe_schedule(None) == "—"


def test_describe_job_status_words():
    assert describe_job_status("ok") == "succeeded"
    assert describe_job_status("error") == "failed"
    assert describe_job_status(None) == "—"
    assert describe_job_status("weird") == "weird"  # unknown marker shown as-is


# --- research notes ---------------------------------------------------------


def test_parse_research_note_labels_known_sections():
    content = (
        "# AAA\n\n"
        "## fundamentals\n"
        "- **Market cap** (2026-05-01): $1.2T\n"
        "## filing\n"
        "- **10-K annual report** (2026-02-15): Risk factors updated.\n"
    )
    sections = parse_research_note(content)
    assert [s.heading for s in sections] == ["Company financials", "Official SEC filings"]
    assert sections[0].intro  # friendly one-liner present
    item = sections[0].items[0]
    assert item.title == "Market cap"
    assert item.date == "2026-05-01"
    assert item.text == "$1.2T"


def test_parse_research_note_handles_empty_and_freeform():
    assert parse_research_note("") == []
    assert parse_research_note(None) == []
    # Content with no recognised structure still comes back as one section.
    sections = parse_research_note("# AAA\n\n(no data available)")
    assert len(sections) == 1
    assert sections[0].items[0].text == "(no data available)"
