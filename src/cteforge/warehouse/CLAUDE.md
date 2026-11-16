# src/cteforge/warehouse/

Warehouse connectors. **HARDENED — every rule below is mandatory.** This module holds
database credentials and executes SQL. Additions require explicit security review with the
user, not a unilateral CLAUDE.md update. These rules override anything in ../../../CLAUDE.md
that conflicts.

## What belongs here (frozen allowlist)
- `base.py` — the `Warehouse` Protocol: `column_types(table) -> dict[str, str]`,
  `explain(sql) -> None` (raises on failure), `run(sql, limit) -> QueryResult`.
- `duckdb.py` — DuckDB implementation.
- (M6) one file per connector: `postgres.py`, `snowflake.py`, `bigquery.py`, `databricks.py`.

## Hard rules
- Credentials MUST come from environment variables or a profile file path passed in by the
  caller. MUST NOT be hardcoded, committed, or defaulted.
- MUST NOT log, print, include in exceptions, or return credentials, connection strings, or
  any value derived from them.
- Connectors MUST NOT build SQL from values. They execute SQL produced by templating/ or
  compose/ as-is. No f-strings with user values; table names in `column_types` go through
  sqlglot identifier quoting or driver parameters.
- `run` MUST enforce a row limit. BigQuery MUST set `maximum_bytes_billed` (M6).
- Documentation MUST recommend read-only roles. Connectors MUST NOT issue DDL/DML; DuckDB
  connections used for queries open read-only where the API supports it.
- Imports: domain and the driver library only. MUST NOT import other cteforge modules.
- Errors are raised as `WarehouseError` (domain/errors.py) with the driver message stripped
  of any connection details.

## If you need something not on the allowlist
STOP. Do not add it. Describe the need to the user and ask for an explicit security review.
The living-document clause used elsewhere does not apply here.

Project root: ../../../CLAUDE.md
