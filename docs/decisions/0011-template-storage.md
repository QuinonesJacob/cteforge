# 0011 — Template storage: named placeholders as text

Status: accepted · 2026-11-15

## Context
The domain layer has no sqlglot dependency, so a stored template must be text. sqlglot cannot parse {{period.start}}.

## Decision
Store templates as SQL text in the definition's source dialect, with named placeholders written in that dialect's own syntax: `$period__start` (DuckDB), `:period__start` (Snowflake, Databricks), `%(period__start)s` (Postgres), `@period__start` (BigQuery). A template is only ever parsed in its source dialect, where these parse back as exp.Placeholder — or exp.Parameter for BigQuery; `templating/render.py:template_slots` handles both, and a test asserts the round trip for every supported dialect. Render: parse in source dialect → replace placeholders with typed literal nodes (exp.Literal + cast to the column's real type) → generate for the target dialect. Never emit SQL that still contains placeholders. Each literal's original form (bare vs cast) is recorded so the round-trip check reproduces it exactly.

## Consequences
- Placeholder syntax follows the source dialect; re-verify the per-dialect round trip when upgrading sqlglot.
- grain cannot be a placeholder; it rebuilds the truncation node (M1.5).
- grain storage (M1.5, 2026-11-16): a placeholder in the unit argument was tried and does not
  survive every source dialect. sqlglot's Databricks/Spark `DATE_TRUNC` builder turns any unit
  argument (incl. `:x`) into an upper-cased `Var`. So nothing is written into the template for
  a grain: the truncation stays exactly as written. Each grain slot names its node by grain ID
  (`<scope>.G<n>`, the same walk order as literal IDs, so the IDs are unchanged in the
  template) and stores the original unit (`VariableSlot.original_text`). Render re-finds the
  node, refuses if its unit no longer matches (template drift), keeps it when the requested
  unit is the original (so same-query-back is exact by construction), and otherwise rebuilds
  the whole node (same node kind and arguments, new unit built as the source dialect's
  parser builds it, e.g. BigQuery WEEK = WEEK(SUNDAY)). Tests assert that the rebuilt node
  re-parses to the same tree in all five dialects.
- column units (2026-11-16): sqlglot reads a bare 1-part name in the unit argument as the
  unit keyword in every dialect, so DuckDB / Postgres / Databricks `DATE_TRUNC(month, ts)`
  with `month` a column would be stored as `DATE_TRUNC('MONTH', ts)` — the column becomes a
  constant, and same-query-back cannot see it (both sides parse the same). parsing finds such
  names in the SQL's tokens; templating refuses the import (AnnotationError: qualify the
  column or write a string unit). Qualified columns (`cfg.month`) stay Columns and are kept.
