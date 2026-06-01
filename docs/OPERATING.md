# Operating guide

A practical runbook for actually running `traders` day to day: what it does, what
to check, and — honestly — how to tell whether it's any good. For the build
history see [slices.md](slices.md); for architecture see [architecture.md](architecture.md).

## What this is (and isn't)

`traders` is a **paper-only research and measurement process**, not a proven
money-maker. It ranks a watchlist by public-domain signals (price momentum,
mean-reversion, value yields), proposes theses with explicit exit conditions,
and scores realized results against a numeric goal. Its value is **discipline and
honest scorekeeping** — not a secret edge. It never places orders; you decide
what to act on and report fills back.

## The daily loop

A cron job (`scripts/daily-run.sh`, 08:00 weekdays) does this automatically:

1. **Ingest** fresh adjusted closes from Tiingo into the `prices` table.
2. **Run** Scout (signal-ranked) → Researcher (EDGAR) → Analyst (signal theses) → PM, writing the day's report.

Your 30-second check each morning:

- Open the UI's **Jobs** page (`http://127.0.0.1:8420/jobs`) or `tail data/cron.log`.
  Confirm the run happened and ended `[ok]` (not `[warn]`/`[error]`/`[skip]`).
- Open **Today** (`/`): do the picks make sense? Are prices fresh
  (`traders metrics` / the latest `prices.day` should be the last trading day)?

Run it on demand any time: `bash scripts/daily-run.sh` (or the bare commands in
the cheat-sheet).

## The habit that makes it worth anything

The scorecard, the optimizer, and the post-mortems are **dead until you feed them
decisions and outcomes**. With zero closed trades the scorecard reads
`INSUFFICIENT_DATA` — which is exactly where a fresh install sits.

So, when a daily pick is something you'd actually take:

- **Act on paper** and record it: the **Positions**/thesis pages have buttons, or
  `traders feedback fill --thesis-id N --price P` (`partial` / `skip` too).
- When your exit condition triggers, **close it**: the Sell button, or
  `traders feedback sell --position-id N --price P`.

Closed trades accumulate into `metrics` and the weekly post-mortems. Aim for a
real sample (dozens of closed trades) before drawing conclusions.

## The weekly review

Cron runs the Reviewer Saturday 09:00 (`scripts/weekly-run.sh`); it writes a
post-mortem for each newly closed position. Then:

- `traders metrics` — the scorecard verdict (`on_track` / `failing` /
  `insufficient_data`) and the four criteria (30-day return, max drawdown, hit
  rate, per-trade Sharpe proxy) vs the goal.
- The UI's **Reviews** page — read the post-mortems; that's where lessons live.

## How to tell if it's *actually* good

Three layers, in increasing difficulty:

1. **Is it running?** — `cron.log` ends `[ok]`, prices are fresh, picks render.
   (Easy; check daily.)
2. **Are the picks sound?** — look-ahead-safe (enforced), data clean (Tiingo is
   split/dividend-adjusted), theses have concrete exits. (Eyeball.)
3. **Does it predict / make money?** — the hard one. This needs **many closed
   trades over months**, judged on:
   - hit rate clearly above 50%,
   - a positive per-trade Sharpe proxy,
   - drawdown within your goal,
   - and — most trustworthy — an **out-of-sample** backtest that doesn't collapse
     vs in-sample:
     `traders backtest --source db --strategy signals --oos-fraction 0.3`.
     Trust the *in-sample-vs-OOS gap* far more than the headline number.

**Read absolute backtest returns as upper bounds.** Survivorship bias (the
watchlist is today's members), a small universe, and a short history all flatter
them. When you eventually tune, the optimizer's OOS gate + trial deflation exist
precisely to stop you fooling yourself — keep the human `--apply` gate.

Honest limiters to keep in mind:

- **Universe is ~40 US large-caps.** Broader is better for cross-sectional
  signals, but Tiingo's free tier is US-EOD only and ~50 symbols/hour (see below).
- **Momentum is regime-dependent** — great in trends, punished at reversals. One
  good stretch is not skill.
- **Paper ≠ live** — no slippage, no fill uncertainty, no emotions.

## Configuration

All under the repo root; secrets/state are gitignored.

- **`.env`** (gitignored) — keys, auto-loaded by every command. Copy from
  `.env.example`. Keys: `TIINGO_API_KEY` (prices), `TRADERS_EDGAR_UA` (EDGAR
  research, a real contact string), `ANTHROPIC_API_KEY` + optional
  `TRADERS_LLM_MODEL` (LLM generators).
- **`data/live-watchlist.json`** — the tickers the cron uses (US symbols; foreign
  venues are skipped by Tiingo's free tier). Edit it to change the universe.
- **`data/strategy.json`** (optional override) — the success thresholds the
  scorecard grades against; otherwise the packaged default is used.
- **`data/traders.db`** — all state (prices, theses, positions, post-mortems).

## Controls

- **On/off:** the UI's **Jobs** page toggles each job, or `traders jobs
  enable|disable {daily,weekly}`. Toggling off makes the cron wrapper skip — the
  crontab entry stays installed. `traders jobs status` prints the current state.
- **Schedule:** `crontab -e` (entries are tagged `# traders-daily` /
  `# traders-weekly`). systemd keeps cron running across WSL restarts.
- **Web UI:** runs as a user systemd service on `http://127.0.0.1:8420`
  (unit at `~/.config/systemd/user/traders-web.service`). Control it with
  `systemctl --user {status,restart,stop,disable} traders-web`; tail logs with
  `journalctl --user -u traders-web -f`. It autostarts on login; run
  `loginctl enable-linger marti` once to also keep it up across logout / at boot.
  (The port lives in the unit's `ExecStart` and in `traders web --port`.)

## Troubleshooting

- **`ingest-prices` skips everything / `[warn]` in the log:**
  - No key → set `TIINGO_API_KEY` in `.env`.
  - **Rate limit** → Tiingo free is ~50 symbols/hour; don't re-ingest repeatedly
    in one hour, and keep the watchlist under ~50 names. It resets within the hour.
  - Foreign ticker (`.PA`, `.DE`, …) → not on the free US-EOD tier; it's skipped.
- **A newly added ticker isn't getting picked:** it needs history (~1 year) for
  the momentum signal. The daily cron fetches `--since ~4y ago`, so it backfills
  on the next run; force it now with
  `traders ingest-prices --watchlist data/live-watchlist.json --since 2021-01-01`
  (mind the hourly cap).
- **Cron didn't run:** `systemctl is-active cron` (should be `active`),
  `crontab -l` (entries present), and check `data/cron.log`.
- **`--generator llm` / `eval-llm` errors:** needs the extra and a key —
  `uv sync --extra llm` and `ANTHROPIC_API_KEY` in `.env`.

## Command cheat-sheet

```bash
# daily, on demand
bash scripts/daily-run.sh
uv run traders run-daily --watchlist data/live-watchlist.json \
    --data-source edgar --rank signals --generator signals

# prices
uv run traders ingest-prices --watchlist data/live-watchlist.json            # refresh
uv run traders ingest-prices --watchlist data/live-watchlist.json --since 2021-01-01  # backfill

# decisions (paper) — populate the track record
uv run traders feedback fill --thesis-id N --price P
uv run traders feedback sell --position-id N --price P

# review & verify
uv run traders metrics
uv run traders backtest --source db --strategy signals --oos-fraction 0.3
uv run traders run-weekly --generator llm

# jobs
uv run traders jobs status
uv run traders jobs disable daily        # or enable

# UI
uv run traders web                       # http://127.0.0.1:8420
```
