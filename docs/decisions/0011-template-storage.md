# 0011 — Template storage: named placeholders as text

Status: accepted · 2026-11-15

## Context
The domain layer has no sqlglot dependency, so a stored template must be text. sqlglot cannot parse {{period.start}}.

## Decision
Store templates as SQL text in the definition's source dialect, with named placeholders written in that dialect's own syntax: `$period__start` (DuckDB), `:period__start` (Snowflake, Databricks), `%(period__start)s` (Postgres), `@period__start` (BigQuery). A template is only ever parsed in its source dialect, where these parse back as exp.Placeholder — or exp.Parameter for BigQuery; `templating/render.py:template_slots` handles both, and a test asserts the round trip for every supported dialect. Render: parse in source dialect → replace placeholders with typed literal nodes (exp.Literal + cast to the column's real type) → generate for the target dialect. Never emit SQL that still contains placeholders. Each literal's original form (bare vs cast) is recorded so the round-trip check reproduces it exactly.

## Consequences
- Placeholder syntax follows the source dialect; re-verify the per-dialect round trip when upgrading sqlglot.
- grain cannot be a placeholder; it rebuilds the truncation node (M1.5).
