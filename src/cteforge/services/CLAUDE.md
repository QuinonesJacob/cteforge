# src/cteforge/services/

Use-cases. Orchestrates the pure core and the I/O adapters. Project root: ../../../CLAUDE.md

## What belongs here (authoritative)
- One file per use-case: `import_query.py`, `render_definition.py` (later
  compile_and_run, propose_definition, ...).
- `connections.py` — opens adapters (e.g. a DuckDB warehouse) for entry points, which
  may not import adapters directly.
- Functions take adapter objects (e.g. a Warehouse) as parameters; no global connections.

If something does not clearly fit one of the above, it does not belong here.
"Not forbidden" is not the same as "allowed."

## Common misplacements
- SQL manipulation — parsing/, templating/, compose/.
- Argument parsing, printing, HTTP/MCP schemas — cli/, mcp/, api/.

## Interactions
Imports everything in the core and adapters. Imported only by cli, mcp, api.
A service never imports another service; promote shared logic to the core.

## If you need to add something not on the allowlist
STOP. Do not place the file here on the assumption it kind of fits. Surface the case to
the user, propose the addition, and update this CLAUDE.md (and the root CLAUDE.md if the
change is project-wide) before placing the new code. The architecture evolves through
conscious decisions, not silent drift.
