# 0016 — Store: SQLAlchemy 2, SQLite locally, Postgres on a server

Status: accepted · 2026-11-16

## Context
Server-first (ADR 0003) needs persistent definitions, versions, proposals, users and settings.

## Decision
- SQLAlchemy 2.x (sync, Core tables) behind a `Store` class in `store/`. One database URL:
  `sqlite:///cteforge.db` by default, `postgresql+psycopg://…` on a server.
- Definition versions are stored as the codec's YAML text (domain/codec.py) plus indexed
  columns (id, version, state, title, keywords). The codec stays the single format.
- Schema created with `metadata.create_all` plus a `schema_meta` version row; Alembic
  migrations are deferred until the first breaking schema change.
- The audit log (ADR 0008) owns its own table in the same database via `audit/`; adapters
  don't import each other.

## Consequences
- Same code path for laptop and server; Postgres-only features are avoided.
