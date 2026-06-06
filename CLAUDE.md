# CLAUDE.md — traders

Conventions for Claude Code sessions in this repo.

## Stack

- Python 3.12+, managed with `uv`. Always use `uv run ...` rather than activating a venv directly.
- SQLite via the stdlib `sqlite3` module. No ORM yet — premature.
- `ruff` for lint and format with project defaults (see `pyproject.toml`).
- `pytest` for tests.
- The core install has **zero dependencies**. Optional extras hold everything else: `realdata` (yfinance) and `web` (FastAPI + uvicorn + Jinja2 + python-multipart, for the local UI in `src/traders/web/`). Web tests `importorskip("fastapi")` so the default `uv run pytest` stays hermetic.

## Workflow

- **Slice-based development.** See `docs/slices.md` for the build order. Don't propose multi-slice mega-changes; propose the next slice.
- **Test-first where reasonable.** New behavior gets a failing test before the implementation. Trivial scaffolding doesn't, but anything with branching or data shape does.
- **Migrations are append-only.** New schema changes land as `migrations/NNN_description.sql`; never edit a shipped migration.
- **No premature abstraction.** v1 of any agent is a plain Python module with a function or two. Classes appear when there's actual state to hold.

## Agents

- Agents are sequential Python modules invoked from the daily orchestrator (`traders.orchestrator`, shipped in slice 8; `run-daily` / `run-weekly` CLI). Not multi-process, not async.
- Each agent reads from and writes to the SQLite db; there's no in-memory pipeline.
- Order: Scout → Researcher → Analyst → Portfolio Manager. Reviewer runs weekly, separately.
- The web UI (`src/traders/web/`) is a separate read/write surface over the same db: it reads agent output through `traders.web.queries` and writes only through `traders.feedback` (agent tables) and the dedicated user-data modules `traders.buylist` / `traders.jobs` — never a parallel write path into the agent tables (`positions` / `theses`).

## Hard rules

- Paper mode only. The system never places real orders. Execution is manual; fills are reported back through the feedback path.
- Plain-text and SQLite only. No cloud-only dependencies for the core flow (external data sources via API are fine).
- All persisted data lives under `data/` (gitignored).
