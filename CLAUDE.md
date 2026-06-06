# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`traders` is an agent-driven, **paper-only** stock research & advisory system: four sequential agents plus a weekly reviewer, all plain Python modules sharing one SQLite database. `README.md` and `docs/` (`architecture.md`, `OPERATING.md`, `slices.md`, `glossary.md`) carry the depth; this file is the working contract.

## Stack

- Python 3.12+, managed with `uv`. Always `uv run ...` — never activate a venv directly.
- SQLite via the stdlib `sqlite3` module. No ORM — premature.
- `ruff` for lint and format (config in `pyproject.toml`); `pytest` for tests.
- The core install has **zero dependencies**. Optional extras hold everything else: `realdata` (yfinance), `web` (FastAPI + uvicorn + Jinja2 + python-multipart), `llm` (anthropic). The `dev` dependency-group already includes the web deps, so `uv run pytest` exercises the web suite; web tests `importorskip("fastapi")` so they skip rather than fail when it's absent.

## Commands

```bash
uv sync                              # core + dev group (pytest, ruff, fastapi, httpx)
uv sync --extra realdata|web|llm     # add an optional adapter's deps

uv run pytest                        # full suite — hermetic, no network
uv run pytest tests/test_analyst.py  # one file
uv run pytest tests/test_analyst.py::test_name -v   # one test

uv run ruff check .                  # lint
uv run ruff format .                 # format

uv run traders <subcommand>          # run the system; see README "CLI" for the full surface
```

There is no coverage gate wired into `pyproject.toml`; run `pytest --cov=src` ad hoc if you need it.

## Architecture

Daily post-close: **Scout → Researcher → Analyst → Portfolio Manager**. **Reviewer** runs weekly. One module per agent in `src/traders/`; the daily orchestrator (`traders.orchestrator`, `run-daily` / `run-weekly`) invokes them in order. Sequential, single-process — no queue, no async, no IPC.

Key conventions that span multiple files (read these before editing):

- **The SQLite db is the pipeline.** Agents never hand objects to each other in memory — each reads the latest (or a `--*-run-id`-targeted) upstream rows, writes its own table, and returns a `run_id`. Schema-shaping discussions live in `docs/architecture.md`'s data-flow diagram.
- **CLI is a Command registry, not one big file.** `cli.py` is a thin dispatcher; subcommands live in `cli_commands/{pipeline,analysis,data,ops}.py`, each exporting a `COMMANDS: list[Command]` (the frozen `Command(name, add_parser, run)` dataclass is in `cli_commands/_common.py`). Registration order is pinned in `cli_commands/__init__.py::_ORDER` so `traders --help` stays stable. To add a subcommand: append a `Command` to the right module **and** its name to `_ORDER`.
- **Protocol-based extension points, stub-by-default.** The Researcher reads through the `DataSource` protocol (`data_sources.py`); the Analyst through a `ThesisGenerator` protocol; the Reviewer through a post-mortem generator. The deterministic **stub is the default**; real adapters are opt-in via flags + extras (`research --data-source {stub,yfinance,edgar}`, `analyse --generator {stub,signals,llm}`). Real adapters **degrade to empty/fallback rather than raise**, so one flaky ticker can't kill a run — and the default test suite stays fully offline (fakes injected, no network).
- **Migrations are append-only and run in order.** `db.apply_migrations` applies each `migrations/NNN_*.sql` not yet in `schema_migrations`, wrapping the DDL **and** its version bookkeeping in one transaction (a failed migration leaves neither half-applied schema nor a recorded version). Never edit a shipped migration — add `migrations/NNN_description.sql`.
- **Concurrency-safe writes.** `db.connect()` sets `PRAGMA busy_timeout` so the web UI and a cron run can touch the db at once; use the `db.immediate()` context manager for any read-modify-write that allocates an id (e.g. run-id allocation) to avoid two writers racing on `MAX(run_id)`.
- **Web UI is a thin surface over the same db** (`src/traders/web/`, `web` extra, binds `127.0.0.1`). It reads agent output through `traders.web.queries` and writes **only** through `traders.feedback` (agent tables) and the dedicated user-data modules `traders.buylist` / `traders.jobs` — never a parallel write path into `positions` / `theses`. Form writes are CSRF-protected. It never runs the agents.
- **The backtest harness (`traders.backtest`) is offline, not a fifth agent.** It composes the *pure* decision functions the live agents expose and **never writes the live tables**; it backs the optimizer's out-of-sample apply gate.

## Workflow

- **Slice-based development.** See `docs/slices.md` for build order. Propose the *next slice*, not multi-slice mega-changes.
- **Test-first where reasonable.** New behavior with branching or data shape gets a failing test first; trivial scaffolding doesn't.
- **No premature abstraction.** v1 of an agent is a plain module with a function or two; classes appear when there's real state to hold.

## Hard rules

- **Paper mode only.** The system never places real orders. Execution is manual; fills are reported back through the `traders.feedback` path.
- **Plain-text and SQLite only** for the core flow. External data sources via API are fine; no cloud-only dependencies.
- All persisted data lives under `data/` (gitignored).
