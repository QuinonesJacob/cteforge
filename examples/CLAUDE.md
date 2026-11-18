# examples/

Runnable sample data for demos and integration tests. Project root: ../CLAUDE.md

## What belongs here (authoritative)
- `seed_warehouse.py` — builds examples/warehouse.duckdb from synthetic data (gitignored output).
- `connections.example.yml` — warehouse connection profiles (ADR 0020): the `demo` DuckDB
  profile (path relative to the file) plus commented Postgres/Redshift/ClickHouse/Snowflake/
  BigQuery/Databricks templates; secrets only by env-var name (`secrets_env`). (The
  ClickHouse trace example lives in tests/fixtures/clickhouse: it needs a ClickHouse server.)
- `queries/*.sql` — sample trusted queries.
- `definitions/<name>.annotations.yml` — the analyst's annotations for `queries/<name>.sql`.
- `definitions/<name>.yml` — definition files generated from them; regenerate with
  `uv run cteforge import examples/queries/<name>.sql examples/definitions/<name>.annotations.yml
  -w examples/warehouse.duckdb -o examples/definitions/<name>.yml` (an integration test
  checks they match a fresh import).

Files that exist now:
| File | What it shows |
|---|---|
| `queries/realization_rate.sql` | CTE chain, IN lists, typed range, a month truncation; its joins go through CTEs whose keys pass through from `deals`, `accounts`, `invoices` (mined as base-table join edges) |
| `definitions/realization_rate.annotations.yml` / `.yml` | the analyst's annotations and the generated definition |
| `definitions/realization_rate_grain.annotations.yml` | same query plus a `grain` variable (M1.5, `deals_in_region.G1`); definition id `realization_rate_by_grain`; no generated `.yml` |
| `queries/booked_vs_collected.sql` | one period bound in two CTEs, strptime text with a suggested format, a number inside arithmetic with a `unit` |
| `definitions/booked_vs_collected.annotations.yml` / `.yml` | its annotations and generated definition |
| `connections.example.yml` | profile file for `CTEFORGE_CONNECTIONS` |
| `seed_warehouse.py` → `warehouse.duckdb` | synthetic tables `accounts`, `deals`, `invoices` |

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
