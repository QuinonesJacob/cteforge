# examples/

Runnable sample data for demos and integration tests. Project root: ../CLAUDE.md

## What belongs here (authoritative)
- `seed_warehouse.py` — builds examples/warehouse.duckdb from synthetic data (gitignored output).
- `queries/*.sql` — sample trusted queries.
- `definitions/<name>.annotations.yml` — the analyst's annotations for `queries/<name>.sql`.
- `definitions/<name>.yml` — definition files generated from them; regenerate with
  `uv run cteforge import examples/queries/<name>.sql examples/definitions/<name>.annotations.yml
  -w examples/warehouse.duckdb -o examples/definitions/<name>.yml` (an integration test
  checks they match a fresh import).
- Examples: `realization_rate` (CTE chain, IN lists, typed range); `booked_vs_collected`
  (one period bound in two CTEs, strptime text with a suggested format, a number inside
  arithmetic with a `unit`).

If something does not clearly fit one of the above, it does not belong here.
"Not forbidden" is not the same as "allowed."

## Common misplacements
- Real company data or queries — never. Synthetic data only.
- Test fixtures — tests/.

## If you need to add something not on the allowlist
STOP. Do not place the file here on the assumption it kind of fits. Surface the case to
the user, propose the addition, and update this CLAUDE.md (and the root CLAUDE.md if the
change is project-wide) before placing the new code. The architecture evolves through
conscious decisions, not silent drift.
