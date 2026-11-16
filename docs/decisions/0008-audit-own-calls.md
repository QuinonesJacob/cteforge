# 0008 — Audit our own calls; tag SQL; don't duplicate warehouse logs

Status: accepted · 2026-11-15

## Context
Warehouses already log every query, but not the question, definition, variables or trust label.

## Decision
Log cteforge's own tool calls with result metadata (not rows). Tag compiled SQL with /* cteforge: <def> v<n> compile_id=... */ so warehouse logs can be joined to ours.

## Consequences
- Full traceability without duplicating query history or storing sensitive results.
