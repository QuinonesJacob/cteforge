# 0001 — Build standalone; don't fork WrenAI

Status: accepted · 2026-11-15

## Context
WrenAI (Apache-2.0) offers git-based context, an MCP server, memory recall and 22+ connectors, but everything is built around its MDL semantic model: agents query modeled objects, not raw SQL.

## Decision
Build cteforge as an independent project. Borrow ideas (file layout, contribution rules), not the engine.

## Consequences
- No large Rust/DataFusion codebase to maintain around an idea we'd remove.
- We build our own warehouse connectors (scoped to 5; ADR 0004).
- Optional WrenAI/dbt import/export can come later for interoperability.
