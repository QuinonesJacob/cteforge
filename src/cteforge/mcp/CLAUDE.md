# src/cteforge/mcp/  — NOT STARTED (M4)

MCP server. Thin adapter over services; tool list in DESIGN §6. Project root: ../../../CLAUDE.md

## What belongs here (authoritative)
- Server setup, tool definitions/descriptions, request/response mapping.

If something does not clearly fit one of the above, it does not belong here.
"Not forbidden" is not the same as "allowed."

## Common misplacements
- Business logic — services/.

## Interactions
Imports services (and domain for types/errors only).

## If you need to add something not on the allowlist
STOP. Do not place the file here on the assumption it kind of fits. Surface the case to
the user, propose the addition, and update this CLAUDE.md (and the root CLAUDE.md if the
change is project-wide) before placing the new code. The architecture evolves through
conscious decisions, not silent drift.
