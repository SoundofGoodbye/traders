# Backlog — veteran-investor persona review

Candidate work distilled from a long-term equity-investor review of the project
(2026-06-05). The reviewer walked the code, docs, and every web page in the
persona of a fundamentals-driven, business-owner-mindset investor.

This is a **pool of prioritized candidates**, not a committed sequence. Per the
slice workflow (`CLAUDE.md`), propose and ship **one** slice at a time — pull the
next item from here, don't batch. Shipped slices are 0–32 (`slices.md`); new work
is slice 33+.

**Legend** — Priority: P0 (credibility-critical) · P1 (high leverage) · P2 (depth/breadth).
Size: S (hours) · M (a slice) · L (multi-slice; decompose first).

---

## Keep / don't regress

The review praised these. Any improvement below must preserve them:

- **Paper-only + human `--apply` gate.** Never auto-execute.
- **Mandatory pre-committed exit** on every thesis (the unit of work).
- **Automatic weekly post-mortems** (`reviewer` → `/reviews`) — the single most-valued habit.
- **Backtest honesty:** OOS gate + Deflated Sharpe trial-deflation (anti-fishing), absolute returns read as upper bounds.
- **Look-ahead-safe signal contract** (`closes_before`, `(rows, as_of)`).
- **Auditability:** thesis → research note → cited sources.
- **Zero-dep core + optional extras + hermetic tests.**
- **Plain-English layer** — keep it, but make it as honest as the docs (see B8/B9).

---

## P0 — Fundamental credibility (fixes the "value-trap generator")

The review's sharpest criticism: the `value` thesis flags a name as cheap from a
**single current `yfinance .info` snapshot** stamped with an `as_of` date
(`fundamentals.py`, slice-25 caveat; consumed in `signals_thesis._value_thesis`),
with **no quality gate and no notion of worth** — then narrates "looks cheap …
the plan is to buy" to a beginner. Cheap stocks are usually cheap for a reason.
This cluster turns "statistically cheap" into something defensible.

### B1 — Period-by-period fundamentals ingestion  · P0 · L  · ✅ shipped (slice 33)
- **Problem:** fundamentals are a single point-in-time-today snapshot, not a historical series. Blocks quality scoring and true value.
- **Proposal:** ingest multi-period statements (income / balance / cash-flow) keyed by fiscal period; store period-keyed rows alongside the existing snapshot table; behind the `realdata` extra, injected fetcher (mirror the slice-25 pattern). Design the store provider-agnostic.
- **Why:** foundation that unblocks B2–B4, B6, B15.
- **Depends on:** — (extends slice 25).
- **Shipped:** slice 33 — `traders.fundamental_periods` + migration `008`. Schema, idempotent store, look-ahead-safe as-of accessors (`load_periods_asof` / `latest_periods`, filing-date or reporting-lag gated), `ingest-fundamental-periods` CLI, hermetic tests. **Next:** B2 consumes it.

### B2 — Quality / moat screen (ship the deferred Piotroski)  · P0 · M  · 🟡 Piotroski shipped (slice 34); ROIC/coverage open
- **Problem:** "cheap" has no quality gate. Piotroski F-score is explicitly deferred (needs period-by-period statements).
- **Proposal:** Piotroski F-score + ROIC/ROE trend, gross-margin stability, interest coverage / debt maturity. Add to `signals_lib`.
- **Why:** separates cheap-and-good from cheap-and-melting. This is the #1 fix.
- **Depends on:** B1.
- **Shipped:** slice 34 — `traders.quality`: the 9-test Piotroski F-score over the slice-33 series, with a look-ahead-safe `piotroski_for(conn, ticker, as_of=...)` and a `computable` denominator for sparse data. Landed in its own module (not `signals_lib`) to keep that a leaf — it needs the `FundamentalPeriod` type. **Still open:** ROIC/ROE trend, interest coverage, debt-maturity — extend `traders.quality` on the same pattern. **Next:** B4 wires the score into the value thesis as a gate.

### B3 — Margin-of-safety / intrinsic-value estimate  · P0 · M/L
- **Problem:** no concept of *worth*; "cheap" is a ratio vs itself, not price vs value.
- **Proposal:** transparent reverse-DCF / normalized-earnings estimate per name; surface "price implies ~X% growth" and a buy-below band. Assumptions explicit and editable.
- **Why:** the line between investing and trading.
- **Depends on:** B1.

### B4 — Gate the `value` thesis on quality + margin of safety  · P0 · S
- **Problem:** value thesis fires on ≥2-of-3 cheap flags alone (`signals_thesis._value_thesis`).
- **Proposal:** require cheap **and** quality-pass **and** margin-of-safety; derive conviction from margin of safety, not flag count.
- **Depends on:** B2, B3.

---

## P1 — Owner mindset / cadence (trader → investor)

The engine thinks in days/weeks (12-1 momentum, RSI, 20-day z, −8% stop); the
persona thinks in years and businesses.

### B5 — Buy-list with price triggers  · P1 · M
- **Problem:** the daily rotation (`scout.py`) nudges daily action; there's no "wait for my price."
- **Proposal:** a user buy-list of names + target buy-below price; surface/alert when hit; de-emphasize daily candidate churn.
- **Why:** flips the psychology from "what do I buy today" to "what am I waiting for" — the review's highest-leverage UX change.
- **Depends on:** stands alone with manual targets; richer with B3.

### B6 — Thesis-intact monitoring (business-level)  · P1 · M
- **Problem:** exits are price/stop/time only; nothing checks whether the *reason to own* still holds.
- **Proposal:** per-thesis "intact checks" (earnings still growing, margin not collapsing, no debt/cut event); flag a broken premise regardless of price.
- **Depends on:** B1.

### B7 — Longer-horizon, total-return framing  · P1 · S/M
- **Problem:** framing is short-horizon; no dividends/total return, no multi-quarter holding view.
- **Proposal:** total return incl. dividends in `metrics`/positions; report holding-period returns; surface longer windows.

---

## P1 — Honesty gap in the UI (close the docstring ↔ screen gap)

The code's caveats are honest; the screen isn't. Make the UI as truthful as the docs.

### B8 — Surface data caveats at the point of claim  · P1 · S
- **Problem:** snapshot/US-EOD caveats live in docstrings/`OPERATING.md`, not where the claim is rendered (`today.html`, `web/explain.py`).
- **Proposal:** inline caveat badges ("from a current snapshot, not audited history"; "US end-of-day prices"). Cheap, high-trust.

### B9 — Reframe **Today** to dampen the daily-action reflex  · P1 · S/M
- **Problem:** Today reads as a daily buy-list with "ride the uptrend" nudges to a beginner — manufactures overtrading.
- **Proposal:** lead with "no action unless it meets your plan"; split "for your homework" from "act now"; lower the urgency while keeping plain English.

### B10 — Conviction that means something  · P1 · S
- **Problem:** conviction is mechanical (e.g. momentum ≥25% → 5). It implies judgment it doesn't have.
- **Proposal:** fold quality/margin-of-safety into conviction **or** relabel the field "signal strength" to stop overclaiming.
- **Depends on:** richer version needs B2/B3.

---

## P2 — Data sources & breadth

### B11 — Fix or stop advertising the EU universe  · P2 · S–L
- **Problem:** watchlist advertises S&P 100 + EuroStoxx 50, but Tiingo's free tier skips foreign venues → ~40 US names actually traded. `OPERATING.md` admits it; the watchlist doesn't.
- **Proposal:** add an EU-capable price source, **or** trim/label the watchlist so advertised = actual. Stretch: broaden to mid/small caps (where mispricing lives) — needs a better provider.

### B12 — Researcher reads filing *content*, not just titles  · P2 · M/L
- **Problem:** `research.render_content` surfaces filing/news **titles + snippets only**; EDGAR bodies go unread. The LLM thesis is only as good as this thin note.
- **Proposal:** pull and extract key sections (risk factors, MD&A, segment data) or full-text snippets for the Analyst/LLM to use.

### B13 — A fundamentals provider you'd stake money on  · P2 · M
- **Problem:** yfinance is scraped/unofficial/fragile — fine for a hobby quote, not to underwrite a buy.
- **Proposal:** evaluate a real provider (even paid) behind the same `DataSource` pattern; keep yfinance as the free default.

---

## P2 — Portfolio-level view

### B14 — Exposure view: sector / correlation / concentration  · P2 · M
- **Problem:** PM does a concentration check, but the UI has no portfolio-level exposure picture.
- **Proposal:** sector weights, pairwise correlation/cluster, single-name concentration on a portfolio/Positions surface.

### B15 — Capital allocation & insider signals  · P2 · M
- **Problem:** no view of buybacks, dividend history, insider buying, or capital-allocation track record — core to owner-mindset judgment.
- **Proposal:** ingest + surface per name in research.
- **Depends on:** B1 / data.

---

## Recommended next slice

**B1 shipped as slice 33** (period-by-period fundamentals: schema, ingestion,
look-ahead-safe accessors). The root unlock is in place.

**Next: B2 (quality / moat screen — ship the deferred Piotroski).** It's the first
consumer of slice 33's `latest_periods` series, and together with **B4** (gate the
`value` thesis on quality + margin of safety) it closes the value-trap criticism
end-to-end — the review's sharpest point. B3 (margin-of-safety estimate) can run
in parallel; it also reads the slice-33 series.
