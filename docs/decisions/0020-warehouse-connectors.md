# 0020 — Warehouse connectors as optional extras; profiles reference secrets by env var

Status: accepted · 2026-11-16

## Decision
- Connectors: DuckDB (core), Postgres (`psycopg`), Redshift (`redshift_connector`; its own
  dialect since ADR 0022), Snowflake (`snowflake-connector-python`), BigQuery
  (`google-cloud-bigquery`), Databricks (`databricks-sql-connector`), ClickHouse
  (`clickhouse-connect`, HTTP; ADR 0025). All Apache-2.0/BSD-style. Non-DuckDB drivers are optional
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

## Addendum (2026-11-16): statement timeouts
- Each profile has `statement_timeout_s` (default 60; `null` turns it off), enforced inside
  the connector: DuckDB interrupts the connection from a timer thread; Postgres sets a
  transaction-local `statement_timeout` (`set_config(..., true)`) in every READ ONLY
  transaction (Redshift the same, in its own `BEGIN READ ONLY` per call, ADR 0022);
  Snowflake sets the session parameter `STATEMENT_TIMEOUT_IN_SECONDS`;
  BigQuery sets `job_timeout_ms` (where the client supports it), waits at most that long
  in `job.result(timeout=...)` and cancels the job on timeout; Databricks passes the
  session configuration `STATEMENT_TIMEOUT`; ClickHouse sends `max_execution_time` with
  every request (ADR 0025). A timed-out statement raises
  `WarehouseError("query timed out after Ns")`. Only the DuckDB timeout is exercised
  against a real engine; the others are unit-tested against fake drivers.
- DuckDB error messages are scrubbed of host paths (database file and directory, home
  directory) and the OS user name before they reach a client.

## Consequences
- Only DuckDB is integration-tested in CI; other connectors are unit-tested against fake
  driver modules and need a real warehouse to validate.
