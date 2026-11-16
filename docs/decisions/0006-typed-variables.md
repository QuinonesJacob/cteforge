# 0006 — Typed variables; column type from the warehouse

Status: accepted · 2026-11-15

## Context
A value like "2026-Q3" is not SQL, and timestamp syntax differs per warehouse.

## Decision
A small set of variable types, each with one strict input format and one render rule per dialect. column is annotated (person or LLM); column_type is always read from the warehouse.

## Consequences
- Invalid inputs are rejected with clear errors that agents can correct.
- Type list: date_range, date, grain, text_list, text, number, boolean.
