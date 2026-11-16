# 0006 — Typed variables; column type from the warehouse

Status: accepted · 2026-11-15

## Context
A value like "2026-Q3" is not SQL, and timestamp syntax differs per warehouse.

## Decision
A small set of variable types, each with one strict input format and one render rule per dialect. column is annotated (person or LLM); column_type is always read from the warehouse.

## Consequences
- Invalid inputs are rejected with clear errors that agents can correct.
- Type list: date_range, date, grain, text_list, text, number, boolean.
- Refined by 0014 (plain values; `format`, `unit`) and 0015 (analyst decides; structural facts; expression types).
- grain (M1.5): input exactly `day | week | month | quarter | year` (lower case). It names
  date truncation nodes by grain ID and has no column-type check. Storage and rendering:
  ADR 0011. Week start per warehouse: DESIGN §13.
- grain coverage (2026-11-16): a grain variable must name every truncation in the query to
  its original unit, or the query can fail (GROUP BY copy) or silently mix periods (two CTEs
  joined on the period, UNION branches). Truncations named by another grain variable count
  as covered. Anything left is a `grain_partial` issue: an error at import, a warning at
  render when a stored definition changes such a grain. A truncation whose unit is a column
  (`DATE_TRUNC(cfg.month, ts)`; a bare `month` in DuckDB / Postgres / Databricks, where the
  unit is a string expression) reports `unit = "column <name>"` and is never a grain.
