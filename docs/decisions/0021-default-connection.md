# 0021 — The admin sets the default connection; the dialect follows the connection

Status: accepted · 2026-11-17 · supersedes the ADR 0018 addendum "per-user default SQL dialect"

## Context
A connection (warehouse profile) already has a dialect: a Postgres connection is Postgres.
Asking each person for a "default dialect" duplicated that and could disagree with it. The
team's warehouses are a team decision, not a personal preference. Teams often have more
than one warehouse (e.g. Postgres + Redshift).

## Decision
- **Default connection** is a team setting, changed only by admins (GUI Settings, API,
  CLI). Resolution: the admin's setting (store) → `CTEFORGE_DEFAULT_PROFILE` → the only
  profile, if exactly one → none.
- **The dialect follows the connection.** When `parse_query` / `propose_definition` get no
  dialect: explicit argument → the chosen profile's dialect (the profile passed, else the
  default connection) → PolicyError saying how to fix it.
- A person may still pick another connection, or another dialect for one import (the
  dropdown stays). A dialect that differs from the connection keeps the ADR 0013 addendum
  behaviour: `dialect_mismatch` warning, checks on transpiled SQL, lower trust.
- The per-user default dialect (account preference, `user_preferences` table,
  `PUT /me/preferences`, `cteforge user dialect`) is removed; it was never released.
- Each definition keeps its own profile; running it always goes to that connection.
  Definitions on different connections can't be combined into one query (separate
  databases); the agent runs them separately.

## Consequences
- One place decides "what our database is"; new users need no setup.
- Changing the default connection doesn't move existing definitions.
