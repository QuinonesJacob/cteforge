# 0004 — Connect to warehouses

Status: accepted · 2026-11-15

## Context
Checking that a query compiles, reading real column types, and re-validating definitions for schema drift all need a connection. Self-hosting keeps credentials inside the company network.

## Decision
Connect directly to Postgres/Redshift, Snowflake, BigQuery, Databricks and DuckDB using free drivers. Read-only roles recommended.

## Consequences
- Supersedes the earlier idea of having no database connection of its own.
- compile_and_run can check and run in one call (no SQL round trip through the agent).
- Connector code is security-sensitive (hardened module).
