# Security & Architecture Audit — `traders`

**Auditor:** Elias Voss, Principal Security Architect (independent white-hat consultant)
**Date:** 2026-06-06
**Commit reviewed:** `34337a8` (branch `master`)
**Method:** Manual review of all trust boundaries, plus five parallel domain passes
(decision pipeline · web surface · external data & secrets · persistence & numerics ·
architecture & maintainability), reconciled and re-verified against source.

> Point-in-time assessment. Severities are calibrated to what this system actually is:
> a **single-user, local, paper-only** research/advisory tool — no real money, no
> multi-tenant model, no untrusted remote requester. The primary threats that matter
> here are (A) adversarial **external data** steering automated decisions, (B) **data
> corruption** that silently misleads the user's advice, and (C) **availability** of
> the daily pipeline. Re-run this audit when those assumptions change (e.g. if the web
> UI is ever bound beyond `127.0.0.1`, or real execution is added).

**Severity legend:** CRITICAL (block) · HIGH (fix soon) · MEDIUM (should fix) ·
LOW (optional / hygiene) · INFO (note). Maintainability findings use a separate scale
where severity = *future* risk, not present exploit.

---

## 1. Verdict

**Posture: strong. Grade B+ — a hardening job, not a remediation job.**

No CRITICAL issues: there is no real-money path, no RCE, and no remote-compromise
surface. The codebase treats external data and model output as untrusted by reflex,
parameterizes every query, gets CSRF and template autoescaping right, and keeps the
paper-only invariant clean. The findings below are a short list against ~19k lines.

**The through-line (the one systemic issue):** the most important domain invariant —
*a position's size is a bounded, positive fraction of NAV* — is enforced in exactly
**one** place: the LLM thesis generator's Python clamp (`llm_thesis.py:110-113`). It is
enforced **nowhere on the path that actually writes a position.** The clamp is a
convention on one optional code path, not a property of the system. Three of the five
review passes reached this gap independently from different files. Fixing it (H1 + M3)
is the highest-value work in this report.

---

## 2. Architecture map

```
PRIMITIVE LAYER (pure, look-ahead-gated, one SELECT + math, no mutation):
  prices · fundamentals · fundamental_periods · quality · valuation
  signals_lib · exposure · intact · deflated_sharpe

AGENT PIPELINE (sequential Python; SQLite is the only bus — no in-memory pipeline):
  Scout → Researcher → Analyst → Portfolio Manager        [run_daily]
  Reviewer                                                 [run_weekly]
  orchestrator.py owns order; the CLI calls it (no duplicated orchestration)

PLUG POINTS (Protocols, dependency-injected, hermetic by default):
  DataSource:          stub | yfinance | edgar | edgar-full
  ThesisGenerator:     stub | signals | llm
  PostMortemGenerator: stub | llm

SURFACES over the same db:
  CLI (cli.py)      — every command
  Web (web/app.py)  — reads via web.queries; writes via feedback / buylist / jobs

TRUST BOUNDARIES (where untrusted data enters):
  [A] external feeds (yfinance / EDGAR / Tiingo / Stooq) → db → notes → LLM → advice
  [B] LLM output → theses → positions → metrics
  [C] human feedback (CLI + web POST) → positions
```

Every finding below lives on boundary **[A]→[B]** or **[C]**.

---

## 3. Findings summary

| #  | Sev  | Finding | Primary location |
|----|------|---------|------------------|
| H1 | HIGH | Position size/price invariants unenforced at the write boundary (systemic) | `feedback.py:102-227`, `web/app.py:117-125`, `analyst.py`, `001_initial.sql:46-56` |
| H2 | HIGH | Quadratic ReDoS + unbounded read in filing-HTML ingestion → daily run hangs | `filing_text.py:20`, `data_sources.py:431,259` |
| M1 | MED  | Untrusted source text laundered *inside* the research-note fence | `research.py:56,61` → `llm_thesis.py:145` |
| M2 | MED  | Silent failure: a total data outage looks identical to "no edge" | `data_sources.py:177/189/347`, `orchestrator.py:72-88` |
| M3 | MED  | No schema guardrails (CHECK on size/price/status, one-open-per-thesis) | `001_initial.sql:30-56` |
| M4 | MED  | Migration apply is non-atomic (implicit commit + separate version insert) | `db.py` (`executescript`) |
| L1 | LOW  | Stooq symbol interpolated unquoted into URL (Tiingo quotes correctly) | `price_ingest.py:37` |
| L2 | LOW  | Tiingo API key transmitted in URL query string | `tiingo.py:87-91` |
| L3 | LOW  | `record_skip` does not reject a thesis that already has an open position | `feedback.py:230-237` |
| L4 | LOW  | run-id derived via `SELECT MAX(...)` + separate INSERT (non-atomic) | scout/research/analyst/portfolio |
| L5 | LOW  | No security response headers / CSP (matters only if bound beyond localhost) | `web/app.py:81` |
| L6 | LOW  | `sources` rendered without URL-scheme validation (safe today; pre-emptive) | `web/queries.py` `parse_sources`, `thesis_detail.html` |
| L7 | LOW  | Buy-list ticker accepts any string / length | `web/app.py:336-343` |

---

## 3.1 H1 — The sizing invariant isn't real until it's enforced where you write

**Severity:** HIGH (data integrity of the entire decision-support surface)

**Evidence (verified against source):**
- `_open_position` (`feedback.py:102-117`) inserts `size_pct` and `price` with **no
  validation**.
- `record_fill` (`feedback.py:151`) accepts any `size_pct` override; `record_partial`
  (`feedback.py:204`) accepts any size and never checks it is ≤ `suggested` or even
  positive.
- The web form parser `form_float` (`web/app.py:117-125`) does `float(value)` and
  nothing else — no range check.
- Schema (`001_initial.sql:46-56`): `size_pct REAL NOT NULL`, `entry_price REAL`
  (nullable) — **no CHECK constraints**.

**Exploit chain (no external attacker required):** a CSRF-valid POST to
`/theses/{id}/fill` with `size_pct=999` (or `-5`, or `price=-10`) → `form_float`
passes it → `_open_position` persists it. Consequences:
- the PM's concentration cap (`portfolio.py`, checked at *decision* time against
  `suggested_size_pct`) is silently bypassed at *fill* time;
- `exposure.py`'s Herfindahl index and `total_size_pct` are corrupted (a negative size
  poisons the sum);
- a negative `entry_price` makes `compute_pnl_pct` (`post_mortems.py:56`, guards
  `== 0` but not `< 0`) report a **gain as a loss**, which then feeds the Optimizer's
  parameter proposals.

**Why HIGH:** the "threat actor" is the user fat-fingering a form, or any *future*
`ThesisGenerator` that doesn't replicate the clamp. But the blast radius is the whole
decision-support surface silently lying, and the guardrail is a one-line check absent
at the boundary that matters. Classic "invariant enforced by convention in one spot."

**Fix (one slice):** put the invariant where the write is.
```python
# feedback.py — single choke point, _open_position()
if not (price > 0):            raise FeedbackError("price must be > 0")
if not (0 < size_pct <= 100):  raise FeedbackError("size_pct must be in (0, 100]")
```
Also validate at the Analyst insert (so no generator can persist a malformed thesis),
change the pnl guard to `entry_price <= 0`, and back it with **M3** (DB CHECK
constraints). Once the clamp is universal at the persistence layer it becomes a system
property — which also shrinks M1's blast radius to nothing.

---

## 3.2 H2 — A large or hostile 10-K hangs the daily run

**Severity:** HIGH (availability of the automated pipeline)

**Evidence:**
- `filing_text._SCRIPT_STYLE` (`filing_text.py:20`) is
  `r"<(script|style)\b[^>]*>.*?</\1>"` with a backreference + lazy DOTALL. On HTML
  with many unclosed `<script>` tags this is **O(n²)** — measured **8.4 s for ~160 KB,
  85 s at 50k tags**. SEC 10-Ks run 1–10 MB.
- Every fetcher does an **unbounded** `resp.read()` / `json.load(resp)`
  (`data_sources.py:431,259`, `edgar_fundamentals.py:147`, `tiingo.py:94`,
  `price_ingest.py:40`) — no byte cap; a runaway response is read fully into memory
  before parsing.

**Impact:** the Researcher tick doesn't degrade, it *hangs* — minutes per filing, or
OOM. Triggered by external data the system fetches by design on the
`edgar` / `edgar-full` path.

**Fix (one slice):** cap the read first (≈10 MB filing HTML, ≈32 MB JSON; raise on
overflow), then replace the backreference with a non-backtracking two-pass strip
(find open tag → find next close → excise). Capping bytes also bounds the regex input,
so the two fixes reinforce.

---

## 3.3 M1 — Untrusted source text laundered inside the research-note fence

**Severity:** MEDIUM (research integrity; backstopped by the clamp + tool schema)

`render_content` (`research.py:56,61`) interpolates untrusted `DataPoint.title` /
`snippet` straight into the markdown note; both LLM generators then fence the whole
note as one opaque blob (`<research_note>` / `<thesis_text>`,
`llm_thesis.py:145-152`, `llm_postmortem.py:94-113`). The fence guards
note-vs-task, **not source-vs-author inside the note** — a crafted news snippet can
forge a fake `</research_note>`, or simply assert a conclusion that reads as the
Researcher's own ("Analyst consensus: STRONG BUY"). Filing text is tag-stripped, but
news snippets are not guaranteed to be, and prose injection needs no delimiter at all.

Defense-in-depth holds (forced tool-call schema + the Python clamp), so this is MEDIUM
— but it defeats the exact boundary the author built. **Fix:** fence each source field
and neutralize the literal close-tag tokens in `render_content`. (H1 makes the numeric
backstop universal.)

## 3.4 M2 — Silent failure is indistinguishable from "no edge"

**Severity:** MEDIUM (operational observability)

The `except Exception: return []` pattern (`data_sources.py:177/189/347` and the fetch
closures) is **intentional and consistent** — one flaky ticker/API can't kill a daily
run, which is correct. But it logs nothing, and the orchestrator chains run-ids forward
unconditionally (`orchestrator.py:72-88`), so **every data source failing** produces the
same clean "0 recommendations" as a genuine no-edge day. An outage is invisible.

**Fix:** a single `logging.warning` in the swallow paths, plus a non-fatal warning when
a pipeline stage produces zero output. Do not change the contract; add observability.

## 3.5 M3 — No schema-level guardrails

**Severity:** MEDIUM (structural backstop for H1)

`positions.status` has no `CHECK (status IN ('open','closed'))` — a typo'd status falls
through *both* the open and closed scans and silently vanishes from every view. There is
no partial unique index enforcing one open position per thesis (the Python
check-then-insert in `feedback.py:148,196` has a race under concurrent web requests).
`entry_price` is nullable despite always being written.

**Fix:** migration `010_constraints.sql` —
```sql
-- CHECK (status IN ('open','closed')) on positions and theses
-- CHECK (size_pct > 0 AND size_pct <= 100), CHECK (entry_price > 0) on positions
CREATE UNIQUE INDEX uq_positions_open_thesis ON positions(thesis_id) WHERE status='open';
```

## 3.6 M4 — Migration apply is non-atomic

**Severity:** MEDIUM (dev-time robustness; recoverable but wedges startup)

`db.py` runs each migration via `executescript()` (which issues an implicit COMMIT) and
then a *separate* `INSERT` into `schema_migrations`. A multi-statement migration that
fails partway commits its early DDL but never records the version → next startup
re-applies it → "table already exists" → the app won't start without manual recovery.

**Fix:** wrap the `executescript` + version-insert in a single savepoint, or wrap each
migration body in `BEGIN IMMEDIATE; … COMMIT;`.

---

## 3.7 LOW findings

- **L1** — `price_ingest.py:37` interpolates `symbol` into the Stooq URL **unquoted**,
  while `tiingo.py:90` correctly uses `urllib.parse.quote`. Latent param-injection;
  symbols are config-sourced today. **Fix:** `urllib.parse.quote(symbol, safe="")`.
- **L2** — `tiingo.py:87-91` sends `TIINGO_API_KEY` as `?token=…` in the URL; it lands
  in server/proxy access logs. **Fix:** send via `Authorization: Token <key>` header.
- **L3** — `record_skip` (`feedback.py:230-237`) does not check for an existing open
  position, so a skip can be logged for a thesis already filled — misleading audit
  trail. **Fix:** reject when `_open_position_for_thesis` is not None.
- **L4** — run-ids are derived via `SELECT MAX(...)` + a separate INSERT in
  scout/research/analyst/portfolio — non-atomic. No concurrency today; theoretical.
  **Fix:** `BEGIN EXCLUSIVE` around the read+write, or `AUTOINCREMENT` PKs.
- **L5** — no `X-Content-Type-Options` / `X-Frame-Options` / CSP headers
  (`web/app.py:81`). Matters only if the app is ever bound beyond `127.0.0.1`.
  **Fix:** a small response-header middleware (no new deps).
- **L6** — `parse_sources` coerces source URLs to `str` with no scheme validation; safe
  today (autoescape, rendered as text, no `<a href>`), but pre-emptive before any future
  link wrap. **Fix:** allow only `http`/`https` schemes in `parse_sources`.
- **L7** — buy-list ticker (`web/app.py:336-343`) has no format/length validation.
  **Fix:** `^[A-Z0-9.^=]{1,12}$`.

---

## 4. Maintainability findings (severity = future risk)

- **D1 [HIGH-maint] `cli.py` is a god file.** 1410 lines (project limit is 800),
  `main()` ~657 lines tangling argparse + dispatch + presentation, with duplicated
  accepted/rejected print loops. Every slice appends to it. **Fix incrementally:** peel
  command bodies into a `cli_commands/` package one at a time; first move the duplicated
  formatting into `reports.py` where the renderers already live.
- **D2 [MED-maint] `_extract_tool_input` is byte-identical** in `llm_thesis.py:83` and
  `llm_postmortem.py:63`, and it parses untrusted model output at the trust boundary. A
  hardening fix to one copy silently misses the other. Both already inherit
  `LLMGenerator`. **Fix:** move it to `traders/llm.py`.
- **D3 [MED-maint] Three EDGAR fetchers** re-implement UA-gate + `urllib` request +
  ticker→CIK map (`data_sources.py:229,410`, `edgar_fundamentals.py:128`) with
  *already-inconsistent* timeouts (10/20/15 s). SEC compliance lives here; a
  rate-limit/back-off fix must land in three places. **Fix:** extract
  `traders/edgar_http.py` (`edgar_ua()`, `edgar_get_json()`, shared `load_cik_map`).
- **D4 [LOW-maint]** `CLAUDE.md` says the web UI "writes only via `traders.feedback`,"
  but `buylist`/`jobs` are legitimate user-data write paths (the real invariant — no
  parallel path into `positions`/`theses` — *is* intact). Stale docs become bad
  refactors. **Fix:** one-line correction.
- **D5 [LOW-maint]** `web/app.py:58` reaches into private `metrics._holding_days` —
  hidden cross-module coupling. **Fix:** promote to public `metrics.holding_days`.
- **D6 [LOW-maint]** `run_backtest` (`backtest.py`) is ~120 lines at 3 nesting levels.
  **Fix:** extract the draft→thesis and open-accepted inner loops as named helpers.

---

## 5. Verified sound (credit where due)

- **Paper-only invariant** — no order-placement path exists anywhere in the tree;
  positions are written only through `feedback.py`, only on explicit user action.
- **SQL** — all ~70 execute sites parameterized; f-strings splice only constant column
  lists (`_SELECT`), never data. `list_theses` and the `fundamentals` /
  `fundamental_periods` WHERE-builders bind every user value.
- **CSRF** — correct double-submit; `hmac.compare_digest` on both signature and token;
  cookie `httponly` + `samesite=strict`; all state-changing POSTs gated.
- **XSS** — Jinja autoescape on; no `| safe`, no `Markup`. `explain.parse_research_note`
  builds plain strings, not HTML.
- **Feedback writes** — atomic per call (both INSERTs before one commit; rollback on
  error via connection close); double-fill and sell-nonexistent guards present;
  `record_sell` takes the position's real `thesis_id` from the DB.
- **Look-ahead safety** — `closes_before` uses strict `<`; fundamentals are
  availability-lagged (90d annual / 45d quarterly).
- **Financial math** — long/short PnL signs correct; max-drawdown walk correct;
  Sharpe / Deflated-Sharpe (PSR) and DCF / Gordon all guard div-by-zero; Herfindahl
  bounded in (0,1].
- **Secrets** — `.env` loader never clobbers the real environment, never logs or
  persists values; `ANTHROPIC_API_KEY` read by the SDK from env; `TRADERS_EDGAR_UA`
  required at construction. EDGAR URLs host-pinned with `zfill(10)` numeric CIKs.
- **Timeouts** present on every `urlopen` (10–20 s).
- **Architecture** — Protocol + dependency-injection at every external boundary keeps
  the default test suite hermetic; orchestrator is the single source of pipeline order;
  migrations are append-only (001–009; none edits a prior file).

---

## 6. Remediation roadmap (slice-sized, priority order)

1. **Slice A — H1 + M3:** validate price/size at `_open_position` and the Analyst
   insert; migration `010_constraints.sql` (CHECKs + partial-unique index); flip the
   pnl guard to `<= 0`. *Closes the systemic gap; shrinks M1 to zero.*
2. **Slice B — H2:** byte-cap every fetch; replace the backreference regex with a linear
   strip.
3. **Slice C — M2 + M1:** logging in the swallow paths + zero-output warnings;
   fence/neutralize source fields in `render_content`.
4. **Slice D — M4 + one-line LOWs:** savepoint around migration apply; L1, L3, L5.
5. **Maintainability track — D1→D3** as their own slices; D4 is a free doc fix to land
   now.

Test-first throughout — each has an obvious failing-test shape (negative size rejected,
pathological HTML returns fast, missing-data run logs a warning).

---

*End of audit. This document is a point-in-time assessment of commit `34337a8`; update
or re-run when the trust assumptions in the preamble change.*
