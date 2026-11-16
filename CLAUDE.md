# cteforge

Open-source server (MCP + GUI) that turns a team's trusted SQL into a library agents can
search, reuse, change safely, and build on. Trusted SQL keeps its logic exactly (formatting is normalized);
only the parts that change are described as typed variables. The analyst decides what
changes (ADR 0015); the SQL determines the format (ADR 0014).

**Read first:** `docs/DESIGN.md` (what and why) and `docs/decisions/` (one ADR per decision).
Don't contradict them silently. If code must change a decision, update DESIGN.md and the ADR
in the same change, and tell the user.

## Stack
Python 3.12 · uv · sqlglot (pinned exactly; ADR 0012) · pydantic v2 · duckdb · pyyaml ·
typer · pytest · ruff · import-linter. GUI (M5) will be React/TS under `web/`.

## Architecture
```
entry points   cli/  mcp/  api/            thin adapters, no logic
                      │
orchestration      services/               use-cases; the ONLY layer that does I/O via adapters
                 ┌────┴──────────────┐
pure core        │                   │  I/O adapters (import only domain; never each other)
 domain ← parsing ← templating ←    │  warehouse/ (HARDENED)  files/  store/  audit/
                 {compose, trust}    │
```
Enforced by import-linter contracts in `pyproject.toml` (`uv run lint-imports`).

## Directory map
- `docs/` — design doc + ADRs (docs/CLAUDE.md)
- `src/cteforge/domain/` — pure models, variable types, definition file codec
- `src/cteforge/parsing/` — sqlglot analysis of SQL; warehouse-type mapping
- `src/cteforge/templating/` — annotations → template, render, round-trip check
- `src/cteforge/compose/` — (M2) combine definitions
- `src/cteforge/trust/` — (M2) trust-label policy
- `src/cteforge/warehouse/` — HARDENED connectors
- `src/cteforge/files/` — definition files on disk
- `src/cteforge/store/` — (M3) database repositories
- `src/cteforge/audit/` — (M3) audit log, SQL tag
- `src/cteforge/services/` — use-cases
- `src/cteforge/cli/` — Typer CLI
- `src/cteforge/mcp/` — (M4) MCP server
- `src/cteforge/api/` — (M5) HTTP API
- `web/` — (M5) GUI
- `examples/` — synthetic DuckDB warehouse, sample queries and definitions
- `tests/` — unit, integration, golden snapshots

Each has its own CLAUDE.md. Read it before adding files there.

## Where things go
- Variable types and their input validation → `domain/variables.py`. Never re-validate inputs elsewhere.
- Definition YAML (de)serialization → `domain/codec.py`. Reading/writing files → `files/`. The M3 store reuses the same codec.
- Any `import sqlglot` → only in `parsing/`, `templating/`, `compose/`. Never in `domain/`.
- Warehouse type string → VariableType / sqlglot DataType → `parsing/types.py`. Not in `warehouse/`.
- Building SQL literals from values → `templating/literals.py` only. Never f-strings or string concatenation of values into SQL.
- Dialect names → `domain/dialects.py` (`Dialect` enum). Don't pass raw dialect strings around.
- Errors → subclasses of `CteforgeError` in `domain/errors.py`.
- Warehouse connections → `warehouse/` only. Services receive a `Warehouse` protocol object.

## Conventions
- Modules and files: snake_case. Pydantic models: PascalCase, frozen where practical.
- Public functions have type hints; core modules are pure (no I/O, no globals, no env reads).
- Tests mirror the source tree under `tests/unit/<module>/`.
- Conventional commits (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`).

## Commands
- `uv sync` — install
- `uv run pytest` — tests
- `uv run lint-imports` — layering contracts
- `uv run ruff check . && uv run ruff format --check .` — lint
- `uv run python examples/seed_warehouse.py` — build `examples/warehouse.duckdb`
- `uv run cteforge --help` — CLI

## Out of scope
- A full semantic layer or BI dashboards. Describe the holes, not the world (ADR 0002).
- Storing result rows. Duplicating the warehouse's own query history (ADR 0008).
