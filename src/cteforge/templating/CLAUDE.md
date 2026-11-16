# src/cteforge/templating/

Turns a trusted query + annotations into a template, and a template + values into SQL.
Pure. Project root: ../../../CLAUDE.md

## What belongs here (authoritative)
- `template.py` — apply_annotations(): replace annotated literals with named
  placeholders (var__field, written in the source dialect's syntax, e.g. $x in DuckDB), producing
  template text in the source dialect (ADR 0011).
- `literals.py` — the ONLY place values become SQL: typed literal nodes + casts per type.
- `render.py` — render(template, definition, values, dialect) → SQL. Never emits placeholders.
- `roundtrip.py` — same-query-back check: serialize → re-parse → render originals → AST compare.

If something does not clearly fit one of the above, it does not belong here.
"Not forbidden" is not the same as "allowed."

## Common misplacements
- Input validation of values — domain/variables.py (call it, don't duplicate it).
- Running SQL to compare results — services/ (uses warehouse/).
- Combining several definitions — compose/ (M2).

## Interactions
Imports domain, parsing. Imported by compose, services.

## If you need to add something not on the allowlist
STOP. Do not place the file here on the assumption it kind of fits. Surface the case to
the user, propose the addition, and update this CLAUDE.md (and the root CLAUDE.md if the
change is project-wide) before placing the new code. The architecture evolves through
conscious decisions, not silent drift.
