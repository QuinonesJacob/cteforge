# 0008 — Audit our own calls; tag SQL; don't duplicate warehouse logs

Status: accepted · 2026-11-15

## Context
Warehouses already log every query, but not the question, definition, variables or trust label.

## Decision
Log cteforge's own tool calls with result metadata (not rows). Tag compiled SQL with /* cteforge: <def> v<n> compile_id=... */ so warehouse logs can be joined to ours.

## Consequences
- Full traceability without duplicating query history or storing sensitive results.

## Addendum (2026-11-18, ADR 0029): what is logged
The log answers "what did agents look up and run, and who changed what". So:
- **Logged:** every MCP tool call, reads included (`search`, `get_definition`,
  `list_joins`, `describe_tables`, `list_proposals`, `list_users`, …); every run
  (`compile_and_run`, `check_and_run`) from any surface, scheduled API / CLI runs
  included; every change from any surface (proposals, decisions, verify, users, roles,
  tokens, profiles, settings, search-index rebuild, library import, revalidation); every
  refused or failed call from any surface.
- **Not logged:** a successful read-only call made by a person through the HTTP API (the
  GUI included) or the CLI (search, the Library, definitions and their history /
  references, parse, the join map, tables, proposals, users / roles / tokens, the audit log
  itself, settings, export).
The adapter sets the surface (`services._audit.surface`: MCP per tool call, the CLI for its
commands, else the API); the read / run / change classification of every use-case is in one
place (`services/_audit.py`) and a test fails when a use-case is not classified. Runs and
changes keep their fail-closed audit writes.

