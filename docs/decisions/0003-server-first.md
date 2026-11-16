# 0003 — Server-first; git is optional

Status: accepted · 2026-11-15

## Context
Business owners of definitions (analysts, finance) don't use pull requests. A prior internal tool proved a hosted server with propose/approve permissions works.

## Decision
Self-hosted server with Postgres (SQLite locally), proposals, approvals, roles, per-definition owners and version history. Git is an optional export/import.

## Consequences
- Setup is one deploy command, not zero setup.
- Version history, diffs and impact preview must be built in the store (M3).
- Until M3, definitions live as files via the same codec the store will use.
