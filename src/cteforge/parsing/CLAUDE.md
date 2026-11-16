# src/cteforge/parsing/

Reads SQL and reports its structure. Pure. Project root: ../../../CLAUDE.md

## What belongs here (authoritative)
- `analyze.py` — analyze(sql, dialect, schema) → QueryAnalysis: CTEs, tables, output
  columns, literals (stable IDs: CTE + DFS ordinal + parent operator), and the column each
  literal is compared to (through wrappers and CTE lineage), plus detected range shapes.
- `types.py` — warehouse type string → VariableType candidates + sqlglot DataType.

If something does not clearly fit one of the above, it does not belong here.
"Not forbidden" is not the same as "allowed."

## Common misplacements
- Producing templates or new SQL — templating/.
- Querying the warehouse for schema — services/ fetches it via warehouse/ and passes it in.

## Interactions
Imports domain. Imported by templating, compose, services.

## If you need to add something not on the allowlist
STOP. Do not place the file here on the assumption it kind of fits. Surface the case to
the user, propose the addition, and update this CLAUDE.md (and the root CLAUDE.md if the
change is project-wide) before placing the new code. The architecture evolves through
conscious decisions, not silent drift.
