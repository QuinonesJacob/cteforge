# 0020 — Warehouse connectors as optional extras; profiles reference secrets by env var

Status: accepted · 2026-11-16

## Decision
- Connectors: DuckDB (core), Postgres/Redshift (`psycopg`), Snowflake
  (`snowflake-connector-python`), BigQuery (`google-cloud-bigquery`), Databricks
  (`databricks-sql-connector`). All Apache-2.0/BSD-style. Non-DuckDB drivers are optional
  extras (`cteforge[postgres]`, …), imported lazily; a missing driver raises WarehouseError
  with the install command.
- Connections are named profiles (`connections.yml` or env), each with a dialect and
  options. Secrets are **referenced by environment-variable name** (`password_env:
  PG_PASSWORD`) and resolved only inside `warehouse/` at connect time; never stored, logged
  or returned.
- `explain` = EXPLAIN (Postgres/Snowflake/Databricks/DuckDB) or a dry run with
  `maximum_bytes_billed` (BigQuery). Sessions are read-only where the warehouse supports it;
  services additionally refuse non-SELECT statements before calling `run`.
- Nightly drift check = re-run each definition's checks against its profile.

## Consequences
- Only DuckDB is integration-tested in CI; other connectors are unit-tested against fake
  driver modules and need a real warehouse to validate.
