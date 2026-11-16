# 0012 — sqlglot as the SQL engine, pinned exactly

Status: accepted · 2026-11-15

## Decision
All parsing, analysis and dialect generation use sqlglot. Pin an exact version and keep golden per-dialect SQL snapshot tests.

## Consequences
- sqlglot output changes between minor versions; upgrades are deliberate and reviewed via snapshot diffs.
