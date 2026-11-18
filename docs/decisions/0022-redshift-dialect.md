# 0022 — Redshift is its own dialect

Status: accepted · 2026-11-17 · amends ADR 0020

## Context
ADR 0020 listed "Postgres/Redshift" behind one psycopg connector and the `postgres`
dialect. Redshift speaks the Postgres wire protocol but its SQL differs (types such as
SUPER/VARBYTE, `GETDATE`, `DATEADD`, `||` and casting rules, no `READ ONLY` transactions in
the Postgres sense, its own system tables). Treating it as Postgres would mis-render and
mis-check SQL.

## Decision
- `Dialect.REDSHIFT` (sqlglot `redshift`): parsing, type mapping, literal rendering and
  golden tests like the other dialects.
- A `redshift` connector in `warehouse/` (HARDENED): read-only by grants (a user with only
  SELECT), single statement, statement timeout, secrets by env-var name, errors scrubbed —
  same guarantees as the others, implemented with what Redshift supports. Driver choice and
  each guarantee's mechanism go through explicit security review with the user before
  commit (warehouse/CLAUDE.md).

## Consequences
- Teams with Postgres + Redshift configure two profiles; each definition maps to one.

## Addendum (2026-11-17): implementation — PENDING security review
Driver: AWS `redshift_connector` (Apache-2.0), extra `cteforge[redshift]`, not psycopg:
psycopg 3 does not support Redshift officially (psycopg issue #122, open), and the Postgres
connector's single-statement mechanism (named prepared statements) makes psycopg send
`DEALLOCATE ALL` after every rollback, which Redshift lacks (only `DEALLOCATE plan_name`).
Without prepared statements psycopg uses the simple protocol for parameterless SQL, which
runs every statement in a string. `redshift_connector` always uses the extended protocol,
so Redshift refuses multi-statement strings ("cannot insert multiple commands into a
prepared statement"). It also supports IAM / Serverless auth, which is NOT enabled: the
connector accepts only an option allowlist and the `password` secret (IAM would let boto3
read AWS credentials outside `secrets_env`; it needs its own review).

Guarantees: Redshift's BEGIN documents `READ ONLY` (correcting the Context above), so every
call is `BEGIN READ ONLY` → `set_config('statement_timeout', ms, true)` → statement →
rollback (autocommit on, so the driver never opens a read-write transaction itself). Grants
remain the guarantee. Column types come from `svv_columns` (late-binding views and Spectrum
tables included); unqualified names resolve via `current_schema()`. Server errors keep only
the message field (no server source paths), then are scrubbed as for every connector. A
statement timeout is SQLSTATE 57014 or "canceled on user's request". Week truncation starts
on Monday. Unit-tested against a fake driver only.
