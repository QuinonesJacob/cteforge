# 0002 — Start from trusted SQL, not a semantic model

Status: accepted · 2026-11-15

## Context
Semantic models must be designed up front and rewrite existing logic; teams already have SQL they trust.

## Decision
Store trusted SQL verbatim and describe only the parts that change (typed variables). "Describe the holes, not the world."

## Consequences
- Useful from the first imported query.
- A semantic model emerges bottom-up (definitions, join map, recipes).
- Must resist scope creep toward describing every table and column.
