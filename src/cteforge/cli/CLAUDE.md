# src/cteforge/cli/

Typer CLI. Thin adapter over services. Project root: ../../../CLAUDE.md

## What belongs here (authoritative)
- `main.py` — commands, each a thin call into services:
  - `parse SQL_FILE [-d dialect] [-w warehouse.duckdb] [--json]` — literal IDs, compared
    types, context facts, wrappers, suggested formats, range candidates, joins.
  - `import SQL_FILE ANNOTATIONS [-d] [-w] [-o out.yml]` — same-query-back checks; prints
    each issue as `<severity>: <code> [variable] [literal]: message`; exit 1 on any error
    issue; writes the file only when no issue is an error.
  - `render DEFINITION [--vars JSON] [-d target] [-w] [--run] [--limit]` — issues go to
    stderr; `--vars` must be a JSON object (decimals parsed exactly).
  Exit codes: 0 ok, 1 cteforge error / error issues, 2 bad command-line input.
  Argument parsing and output formatting only.

If something does not clearly fit one of the above, it does not belong here.
"Not forbidden" is not the same as "allowed."

## Common misplacements
- Any logic beyond calling a service and printing the result — services/.

## Interactions
Imports services (and domain for types/errors only).

## If you need to add something not on the allowlist
STOP. Do not place the file here on the assumption it kind of fits. Surface the case to
the user, propose the addition, and update this CLAUDE.md (and the root CLAUDE.md if the
change is project-wide) before placing the new code. The architecture evolves through
conscious decisions, not silent drift.
