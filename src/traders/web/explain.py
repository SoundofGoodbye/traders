"""Plain-English presentation layer for the web UI.

Turns the agents' terse, jargon-y thesis text into a friendly explanation a
non-expert can act on, and re-shapes a research note's markdown digest into
labelled sections. Pure functions over the `queries` dataclasses and the
stored strings — no database, no FastAPI — so it imports without the `web`
extra and is cheap to unit-test.

This is *non-destructive*: it reads the same `rationale` / `exit_condition` /
research `content` the agents already wrote (and that the CLI and reports still
show verbatim) and derives a readable view. The numbers come from the
deterministic signal templates in `signals_thesis.py`; anything it can't parse
degrades to a sensible generic explanation, and the original line is always
shown alongside.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from traders.post_mortems import compute_pnl_pct
from traders.web.queries import PostMortem, Thesis

# --- thesis explanation -----------------------------------------------------


@dataclass(frozen=True)
class GlossaryItem:
    """One jargon term and its plain-English definition."""

    term: str
    definition: str


@dataclass(frozen=True)
class ThesisExplanation:
    """A thesis re-told for a beginner: what the bet is, when to sell, terms."""

    strategy: str
    sell_when: str
    earnings_warning: str | None
    glossary: list[GlossaryItem]


_TAG_RE = re.compile(r"^\[(?:signal|llm|stub)\]\s*")
_MOMENTUM_RE = re.compile(r"12-1 momentum ([+-]?\d+(?:\.\d+)?)%")
_RSI_RE = re.compile(r"RSI (n/a|\d+(?:\.\d+)?)")
_Z_RE = re.compile(r"20-day z (n/a|[+-]?\d+(?:\.\d+)?)")
_FLAGS_RE = re.compile(r"\((\d)/3 value flags\)")
_STOP_RE = re.compile(r"(\d+)%\s*stop")

# (term, definition, trigger substrings) — included when a trigger appears in
# the thesis's rationale or exit text (matched case-insensitively).
_GLOSSARY: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "12-1 momentum",
        "Price change over the past 12 months, skipping the most recent month "
        "(fresh moves often reverse). A standard way to measure a trend.",
        ("12-1 momentum",),
    ),
    (
        "RSI",
        "Relative Strength Index: a 0–100 gauge of how hot a stock is. "
        "70+ = overbought, 30− = oversold, around 50 = neutral.",
        ("rsi",),
    ),
    (
        "Stop-loss",
        "A pre-set price where you sell to cap your loss — here, a set "
        "percentage below what you paid.",
        ("stop",),
    ),
    (
        "20-day z-score",
        "How far the price sits from its recent 20-day average, in standard "
        "deviations. Very negative = unusually beaten-down.",
        ("20-day z", "z>="),
    ),
    (
        "Mean-reversion",
        "The idea that an unusually low price tends to bounce back toward its average.",
        ("mean-reversion", "reverts"),
    ),
    (
        "Earnings yield (E/P)",
        "Earnings divided by price — the upside-down P/E ratio. Higher = cheaper.",
        ("e/p",),
    ),
    (
        "Book-to-price (B/P)",
        "Book (accounting) value divided by price. Higher = cheaper versus assets.",
        ("b/p",),
    ),
    (
        "FCF yield",
        "Free cash flow divided by market value. Higher = more cash generated per dollar of price.",
        ("fcf",),
    ),
    (
        "Value flags",
        "How many of the three 'cheap' tests (E/P, B/P, FCF yield) the stock passes.",
        ("value flags",),
    ),
    (
        "NAV",
        "Net asset value — the total current value of the paper portfolio.",
        ("nav",),
    ),
)


def strip_tag(rationale: str | None) -> str:
    """Drop a leading ``[signal]`` / ``[llm]`` source tag for display."""
    if not rationale:
        return ""
    return _TAG_RE.sub("", rationale).strip()


def _action_words(direction: str) -> tuple[str, str]:
    """(verb, price-outcome) for a direction, e.g. ('buy', 'rise')."""
    if direction == "long":
        return "buy", "rise"
    if direction == "short":
        return "short-sell", "fall"
    return "trade", "move"


def _rsi_phrase(value: float) -> str:
    if value >= 70:
        return "looking overbought (hot)"
    if value <= 30:
        return "looking oversold (beaten-down)"
    return "neither overbought nor oversold"


def _num(match: re.Match[str] | None) -> str | None:
    """The captured group of a match, unless it's missing or ``n/a``."""
    if match is None:
        return None
    value = match.group(1)
    return None if value == "n/a" else value


def _momentum_strategy(ticker: str, rationale: str) -> str:
    mom = _num(_MOMENTUM_RE.search(rationale))
    rsi = _num(_RSI_RE.search(rationale))
    if mom is None:
        return (
            f"A momentum idea: {ticker} has been trending up, and the bet is the "
            "trend keeps going — so the plan is to buy and ride it."
        )
    tail = ""
    if rsi is not None:
        tail = f" On the 0–100 'how hot' gauge it reads {rsi} — {_rsi_phrase(float(rsi))}."
    return (
        f"{ticker} has climbed {mom.lstrip('+')}% over the past year (leaving out the most "
        "recent month, which often reverses). Strong gains like this tend to keep "
        f"going for a while, so the plan is to buy and ride the uptrend.{tail}"
    )


def _meanrev_strategy(ticker: str, rationale: str) -> str:
    z = _num(_Z_RE.search(rationale))
    rsi = _num(_RSI_RE.search(rationale))
    bits = []
    if z is not None:
        bits.append(f"a 20-day score of {z}")
    if rsi is not None:
        bits.append(f"RSI {rsi}/100")
    detail = f" ({', '.join(bits)})" if bits else ""
    return (
        f"{ticker} has sold off enough to look unusually beaten-down{detail}. The "
        "bet is a short-term bounce back toward its recent average price, so the "
        "plan is to buy."
    )


def _value_strategy(ticker: str, rationale: str) -> str:
    flags = _num(_FLAGS_RE.search(rationale))
    count = flags if flags is not None else "several"
    return (
        f"{ticker} looks cheap on {count} of 3 value yardsticks — earnings, book "
        "value, and cash flow measured against its price. The bet is the market "
        "re-rates it higher over time, so the plan is to buy."
    )


def _generic_strategy(ticker: str, thesis_type: str, direction: str) -> str:
    verb, outcome = _action_words(direction)
    kind = thesis_type or "discretionary"
    return (
        f"This is a {kind} idea. The bet is the price will {outcome}, so the plan "
        f"is to {verb} {ticker}. The reasoning is in the line above."
    )


def _strategy(thesis: Thesis) -> str:
    rationale = thesis.rationale or ""
    # LLM theses carry free-form prose, not the parseable signal templates.
    is_signal = rationale.startswith("[signal]")
    if is_signal and thesis.thesis_type == "momentum":
        return _momentum_strategy(thesis.ticker, rationale)
    if is_signal and thesis.thesis_type == "mean-reversion":
        return _meanrev_strategy(thesis.ticker, rationale)
    if is_signal and thesis.thesis_type == "value":
        return _value_strategy(thesis.ticker, rationale)
    return _generic_strategy(thesis.ticker, thesis.thesis_type, thesis.direction)


def _sell_when(exit_condition: str | None) -> str:
    if not exit_condition:
        return "No specific exit set — re-evaluate at the next review."
    low = exit_condition.lower()
    reasons: list[str] = []
    if "12-1 momentum turning negative" in low:
        reasons.append("the year-long uptrend fades (its 12-1 momentum turns negative)")
    if "20-day mean" in low or "reverts" in low:
        reasons.append("the price climbs back to its recent 20-day average")
    if "valuation re-rates" in low or "cheap flags lapse" in low:
        reasons.append("it stops looking cheap on the value measures")
    if not reasons:
        # No recognised signal trigger (e.g. a custom / LLM-written exit):
        # show it verbatim rather than translating only the stop and dropping
        # the rest of its meaning.
        return f"Sell when: {strip_tag(exit_condition)}"
    stop = _STOP_RE.search(low)
    if stop:
        reasons.append(
            f"you're down {stop.group(1)}% from your buy price (a safety stop to cap the loss)"
        )
    return "Sell if " + ", or ".join(reasons) + "."


def _earnings_warning(rationale: str | None) -> str | None:
    if not rationale or "earnings in" not in rationale.lower():
        return None
    match = re.search(r"earnings in (\d+)d", rationale.lower())
    days = f" (in about {match.group(1)} days)" if match else ""
    return (
        f"Heads-up: this company reports earnings soon{days}, which can swing the "
        "price sharply — size and timing accordingly."
    )


def _glossary_for(text: str) -> list[GlossaryItem]:
    low = text.lower()
    return [
        GlossaryItem(term=term, definition=definition)
        for term, definition, triggers in _GLOSSARY
        if any(trigger in low for trigger in triggers)
    ]


def explain_thesis(thesis: Thesis) -> ThesisExplanation:
    """Translate a thesis into a beginner-friendly explanation + glossary."""
    combined = f"{thesis.rationale or ''} {thesis.exit_condition or ''}"
    return ThesisExplanation(
        strategy=_strategy(thesis),
        sell_when=_sell_when(thesis.exit_condition),
        earnings_warning=_earnings_warning(thesis.rationale),
        glossary=_glossary_for(combined),
    )


# --- thesis-list labels -----------------------------------------------------
# Short, scannable labels for the theses table, where a full paragraph would
# be too much. They translate the raw enum/number columns into plain words.

_CONVICTION_WORDS = {5: "very high", 4: "high", 3: "medium", 2: "low", 1: "very low"}

# (verb, what the bet is) keyed by thesis_type then direction.
_HEADLINE_TAILS: dict[str, dict[str, str]] = {
    "momentum": {"long": "to ride its uptrend", "short": "expecting the slide to continue"},
    "mean-reversion": {"long": "expecting a bounce back up", "short": "expecting it to fall back"},
    "value": {"long": "while it still looks cheap", "short": ""},
}

_TYPE_NAMES = {
    "momentum": "trend-following",
    "mean-reversion": "bounce-back",
    "value": "cheap-stock",
}


def describe_conviction(conviction: int) -> str:
    """A plain word for the 1–5 conviction score (how strong the signal was)."""
    return _CONVICTION_WORDS.get(int(conviction), "—")


def describe_thesis_type(thesis_type: str) -> str:
    """A short, friendly name for a strategy family."""
    return _TYPE_NAMES.get(thesis_type, thesis_type or "discretionary")


def _verb(direction: str) -> str:
    if direction == "long":
        return "Buy"
    if direction == "short":
        return "Short-sell"
    return "Trade"


def thesis_headline(thesis: Thesis) -> str:
    """One short line a beginner can scan: the action and the bet behind it."""
    tail = _HEADLINE_TAILS.get(thesis.thesis_type, {}).get(thesis.direction, "")
    return f"{_verb(thesis.direction)} {thesis.ticker} {tail}".strip()


def describe_position(direction: str | None) -> str:
    """Plain, past-tense word for an open/closed position's direction.

    A position already exists, so this reads as what you *did* ("Bought" /
    "Short-sold"), unlike a thesis's proposed action ("Buy" / "Short-sell").
    """
    if direction == "long":
        return "Bought"
    if direction == "short":
        return "Short-sold"
    return "—"


# --- post-mortems (weekly reviews) ------------------------------------------


@dataclass(frozen=True)
class PostMortemExplanation:
    """A closed trade re-told for a beginner: what was done and how it went."""

    summary: str
    result: str  # "gain" | "loss" | "flat" | "unknown" — also drives styling
    pnl_pct: float | None
    outcome: str  # the reviewer's own outcome line, source tag stripped
    lessons: str  # the reviewer's own lessons line, source tag stripped


def _held_phrase(opened_at: str | None, closed_at: str | None) -> str:
    """`" over N days"` between two stored ISO timestamps, or `""` if unknown."""
    try:
        days = (date.fromisoformat(closed_at[:10]) - date.fromisoformat(opened_at[:10])).days
    except (TypeError, ValueError):
        return ""
    if days <= 0:
        return ""
    return f" over {days} day{'s' if days != 1 else ''}"


_TRADE_NOUNS = {"long": "buy", "short": "short-sell"}


def explain_post_mortem(pm: PostMortem) -> PostMortemExplanation:
    """Translate a weekly post-mortem into a one-line plain-English account."""
    noun = _TRADE_NOUNS.get(pm.direction, "trade")
    opener = f"A {describe_thesis_type(pm.thesis_type)} {noun} on {pm.ticker}."
    pnl = compute_pnl_pct(pm.direction, pm.entry_price, pm.exit_price)
    if pnl is None:
        summary = f"{opener} The result isn't clear because a price is missing."
        return PostMortemExplanation(
            summary=summary,
            result="unknown",
            pnl_pct=None,
            outcome=strip_tag(pm.outcome),
            lessons=strip_tag(pm.lessons),
        )
    result = "gain" if pnl > 0 else "loss" if pnl < 0 else "flat"
    if pm.direction == "short":
        trade = f"Short-sold at {pm.entry_price:.2f}, covered at {pm.exit_price:.2f}"
    else:
        trade = f"Bought at {pm.entry_price:.2f}, sold at {pm.exit_price:.2f}"
    held = _held_phrase(pm.opened_at, pm.closed_at)
    return PostMortemExplanation(
        summary=f"{opener} {trade} — a {pnl:+.1f}% {result}{held}.",
        result=result,
        pnl_pct=pnl,
        outcome=strip_tag(pm.outcome),
        lessons=strip_tag(pm.lessons),
    )


# --- scheduled jobs ---------------------------------------------------------

_DOW_NAMES = {
    "0": "Sunday",
    "7": "Sunday",
    "1": "Monday",
    "2": "Tuesday",
    "3": "Wednesday",
    "4": "Thursday",
    "5": "Friday",
    "6": "Saturday",
}

_JOB_STATUS_WORDS = {
    "ok": "succeeded",
    "error": "failed",
    "warn": "finished with warnings",
    "skip": "skipped (job is off)",
    "running": "running…",
}


def _dow_phrase(dow: str) -> str | None:
    """Plain cadence for a cron day-of-week field, or None if not recognised."""
    if dow == "*":
        return "Every day"
    if dow == "1-5":
        return "Every weekday"
    if dow in ("0,6", "6,0", "0,7", "6,7"):
        return "Every weekend"
    if dow in _DOW_NAMES:
        return f"Every {_DOW_NAMES[dow]}"
    return None


def describe_schedule(expr: str | None) -> str:
    """Turn a 5-field cron expression into a plain-English cadence.

    Falls back to the raw expression for anything it can't confidently read,
    so an unusual schedule is shown honestly rather than mis-described.
    """
    if not expr:
        return "—"
    parts = expr.split()
    if len(parts) != 5:
        return expr
    minute, hour, dom, month, dow = parts
    try:
        time_str = f"{int(hour):02d}:{int(minute):02d}"
    except ValueError:
        return expr
    phrase = _dow_phrase(dow)
    if phrase is None or dom != "*" or month != "*":
        return expr
    return f"{phrase} at {time_str}"


def describe_job_status(status: str | None) -> str:
    """A friendly word for a cron-log status marker (`ok`/`error`/…)."""
    if not status:
        return "—"
    return _JOB_STATUS_WORDS.get(status, status)


# --- research notes ---------------------------------------------------------


@dataclass(frozen=True)
class NoteItem:
    """One bullet in a research section: an optional title/date plus its text."""

    title: str | None
    date: str | None
    text: str


@dataclass(frozen=True)
class NoteSection:
    """A labelled group of research items with a one-line, plain intro."""

    heading: str
    intro: str
    items: list[NoteItem]


# Friendly heading + one-liner for each known data-source section kind.
_KIND_LABELS: dict[str, tuple[str, str]] = {
    "fundamentals": (
        "Company financials",
        "The company's size, earnings, and cash — the basics of its financial health.",
    ),
    "filing": (
        "Official SEC filings",
        "Documents the company filed with the U.S. market regulator (the SEC).",
    ),
    "news": ("Recent news", "Recent headlines about the company."),
}

_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")
_TITLE_RE = re.compile(r"^-\s+\*\*(.+?)\*\*\s+\(([^)]*)\):\s*(.*)$")


def _label_for(kind: str) -> tuple[str, str]:
    return _KIND_LABELS.get(kind.lower(), (kind[:1].upper() + kind[1:], ""))


def parse_research_note(content: str | None) -> list[NoteSection]:
    """Re-shape a research note's markdown digest into labelled sections.

    Understands the structure `traders.research.render_content` emits
    (`# TICKER`, `## kind`, `- **title** (date): snippet`). Lines that don't
    match are kept as plain text, and content that matches nothing at all comes
    back as a single "Notes" section — so old or free-form notes still render.
    """
    if not content or not content.strip():
        return []
    sections: list[NoteSection] = []
    heading, intro = "Notes", ""
    items: list[NoteItem] = []

    def flush() -> None:
        if items or sections or heading != "Notes":
            sections.append(NoteSection(heading=heading, intro=intro, items=list(items)))

    for raw in content.splitlines():
        line = raw.rstrip()
        if not line or line.startswith("# "):  # blank or the `# TICKER` title
            continue
        head = _HEADING_RE.match(line)
        if head:
            flush()
            heading, intro = _label_for(head.group(1))
            items = []
            continue
        title = _TITLE_RE.match(line)
        if title:
            items.append(
                NoteItem(title=title.group(1), date=title.group(2) or None, text=title.group(3))
            )
        else:
            items.append(NoteItem(title=None, date=None, text=line.lstrip("- ").strip()))
    flush()
    return sections
