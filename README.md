# traders

Agent-driven stock research & advisory system. Daily cadence, paper-mode (manual execution with a feedback loop), EU + US large-cap. Four agents — Scout, Researcher, Analyst, Portfolio Manager — plus a weekly Reviewer for post-mortems.

This repo is at slice 0: scaffolding only. No agent logic yet.

## Running

```bash
uv sync
uv run pytest
```

## Layout

- `src/traders/` — package source
- `tests/` — pytest suite
- `migrations/` — SQLite schema migrations, applied in order
- `data/` — local SQLite db lives here (gitignored)
- `docs/` — slice plan, architecture, glossary

See `docs/slices.md` for the build order and `docs/architecture.md` for the agent shape.
<!-- delegation test: 2026-05-19 -->
<!-- delegation smoke test (bigger scope): 2026-05-19 -->
