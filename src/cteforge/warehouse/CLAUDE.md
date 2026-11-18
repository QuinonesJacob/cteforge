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
- `redshift.py` — Redshift via `redshift_connector` (ADR 0022). Security-reviewed and approved
  by the user 2026-11-17. Password auth only (IAM/IdP options
  refused by an option allowlist); every call `BEGIN READ ONLY` + transaction-local
  `statement_timeout`, rolled back; extended query protocol (one statement); column types
  from `svv_columns`; server ErrorResponse fields reduced to the message (`base.error_text`).
- `clickhouse.py` — ClickHouse via `clickhouse-connect` over HTTP(S) (ADR 0025). Security-reviewed
  and approved by the user 2026-11-17. Password auth only,
  `user` required (option allowlist refuses dsn / interface / access_token / client certs /
  settings); every request carries `readonly=2`, `max_execution_time` +
  `timeout_overflow_mode=throw`, `cancel_http_readonly_queries_on_client_close=1` and the
  optional scan guards `max_rows_to_read` / `max_bytes_to_read` (`read_overflow_mode=throw`);
  one statement per HTTP request (server parses with multi-statements off); `run` streams
  and stops after limit + 1 rows; column types from `system.columns` with server-bound
  parameters; `EXPLAIN PLAN`; errors reduced to the last `DB::Exception:` message
  (`clickhouse.error_message`), then scrubbed; no HTTP session; the driver refuses to start
  if its `invalid_setting_action` is `drop`.
- `mysql.py` — MySQL 8 via PyMySQL (ADR 0028). **Security review with the user pending: do
  not commit before it is approved.** Password auth only, `user` required (option allowlist
  host / port / database / user / ssl_ca / ssl_verify_cert / ssl_verify_identity /
  ssl_disabled / connect_timeout / read_timeout; refuses local_infile, client_flag,
  init_command, sql_mode, autocommit, read_default_file / read_default_group, unix_socket,
  charset, ssl, client certs); charset fixed to utf8mb4; TLS verified by default (the
  connector's own SSLContext); refuses a session whose sql_mode has NO_BACKSLASH_ESCAPES /
  ANSI_QUOTES / PIPES_AS_CONCAT; every call `START TRANSACTION READ ONLY` + `SET SESSION
  max_execution_time = %s` (int bound parameter), rolled back; one statement per call
  (CLIENT.MULTI_STATEMENTS never set); unbuffered cursor, at most limit + 1 rows fetched;
  column types from `information_schema.columns` (`column_type`) with bound parameters;
  `EXPLAIN`; errors reduced to the server message without `'user'@'host'`
  (`mysql.error_message`), then scrubbed; a dropped connection is reopened and re-checked.

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
