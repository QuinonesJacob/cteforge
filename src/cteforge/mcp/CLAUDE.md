# src/cteforge/mcp/

MCP server (ADR 0018, DESIGN §6). Thin adapter over services. Project root: ../../../CLAUDE.md

## What belongs here (authoritative)
- `server.py` — `build_server(ctx: AppContext, resolve_user: Callable[[], User]) -> FastMCP`
  registering the tools below; `run_stdio()` used by `cteforge mcp`.
- `descriptions.py` — tool descriptions. These are the agent's instructions: when to call
  each tool, the input formats (from VariableCard.input_format), how to read issues and
  trust labels, and that definition text is data, not instructions (DESIGN §14).

If something does not clearly fit one of the above, it does not belong here.
"Not forbidden" is not the same as "allowed."

## Tools (each maps to one service call; responses are the services' card models as JSON)
search · get_definition · compile_and_run · check_and_run · parse_query ·
propose_definition · revise_definition · propose_join · propose_recipe.
No approve/verify tools (ADR 0007). Errors are returned as tool errors carrying the
CteforgeError message and any Issues, never stack traces or credentials.

## Common misplacements
- Business logic, permission rules — services/ and trust/.

## Interactions
Imports services (and domain for types/errors only). Mounted by api/ for streamable HTTP.

## If you need to add something not on the allowlist
STOP. Do not place the file here on the assumption it kind of fits. Surface the case to
the user, propose the addition, and update this CLAUDE.md (and the root CLAUDE.md if the
change is project-wide) before placing the new code. The architecture evolves through
conscious decisions, not silent drift.
