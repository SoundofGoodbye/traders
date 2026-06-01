#!/usr/bin/env bash
# Optional weekly runner for cron: the Reviewer writes post-mortems for closed
# positions. Uses the LLM generator for real write-ups (needs `uv sync --extra
# llm` + ANTHROPIC_API_KEY in .env). With no closed positions it is a harmless
# no-op; appends to data/cron.log and auto-loads ./.env like any `traders` run.
#
# Example crontab (Saturday 09:00 — adjust for your timezone):
#   0 9 * * 6  /ABSOLUTE/PATH/to/traders/scripts/weekly-run.sh

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1

# cron starts with a minimal PATH; make uv discoverable.
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:$PATH"

LOG="data/cron.log"
{
  echo "===== $(date '+%Y-%m-%d %H:%M:%S %Z') weekly review ====="
  if ! uv run traders jobs check weekly; then
    echo "[skip] weekly disabled via UI"
  elif uv run traders run-weekly --generator llm; then
    echo "[ok] weekly review complete"
  else
    echo "[error] run-weekly failed (for real LLM post-mortems: uv sync --extra llm + set ANTHROPIC_API_KEY in .env)"
  fi
  echo
} >>"$LOG" 2>&1
