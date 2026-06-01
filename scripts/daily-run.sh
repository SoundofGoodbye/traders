#!/usr/bin/env bash
# Optional daily runner for cron. The repo leaves cron wiring to the operator;
# this convenience wrapper refreshes prices, then produces the day's report,
# appending to data/cron.log. It auto-loads ./.env (TIINGO_API_KEY,
# TRADERS_EDGAR_UA) like any `traders` run.
#
# Example crontab (08:00 on weekdays — adjust the time for your timezone and for
# when your data provider's EOD prices settle):
#   0 8 * * 1-5  /ABSOLUTE/PATH/to/traders/scripts/daily-run.sh

# Resolve the repo root from this script's own location (portable).
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1

# cron starts with a minimal PATH; make uv discoverable.
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:$PATH"

LOG="data/cron.log"
WL=()
[ -f data/live-watchlist.json ] && WL=(--watchlist data/live-watchlist.json)

{
  echo "===== $(date '+%Y-%m-%d %H:%M:%S %Z') daily run ====="
  if ! uv run traders jobs check daily; then
    echo "[skip] daily disabled via UI"
  else
    echo "--- ingest-prices ---"
    uv run traders ingest-prices "${WL[@]}" \
      || echo "[warn] ingest-prices failed (set TIINGO_API_KEY in .env to enable price refresh)"
    echo "--- run-daily (ranked Scout + signal theses, EDGAR research) ---"
    if uv run traders run-daily "${WL[@]}" --data-source edgar --rank signals --generator signals; then
      echo "[ok] daily run complete"
    else
      echo "[error] run-daily failed"
    fi
  fi
  echo
} >>"$LOG" 2>&1
