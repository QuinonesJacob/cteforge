# 0005 — The LLM picks values; the tool renders SQL

Status: accepted · 2026-11-15

## Context
Each time an LLM retypes a long query it may silently change logic.

## Decision
Agents call by reference: definition IDs + variable values (+ an optional small final SELECT). The tool renders SQL deterministically. Full SQL from an agent only appears for exploratory queries.

## Consequences
- Verified SQL never passes through the LLM.
- Strict input formats are required (ADR 0006).
