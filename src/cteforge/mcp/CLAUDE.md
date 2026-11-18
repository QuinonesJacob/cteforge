# src/cteforge/mcp/

MCP server (ADR 0018, DESIGN §6). Thin adapter over services. Project root: ../../../CLAUDE.md

## What belongs here (authoritative)
- `server.py` — `build_server(ctx: AppContext, resolve_user: Callable[[], User], *,
  transport_security=None) -> FastMCP` registering the tools below; `run_stdio()` used by
  `cteforge mcp` (acts as CTEFORGE_USER via `services.auth.local_user(create=False)`, re-read
  per call: the user must exist, an empty store is refused; starts meaning search in the
  background — `search_index.start_background` — so no tool call waits for the model);
  `http_app(server, *, path="/") -> Starlette` for the API to mount (mounted at `/mcp` →
  endpoint `/mcp/`; the parent app's lifespan must run `server.session_manager.run()`,
  Starlette does not run a mounted app's lifespan); `current_request()` — the HTTP request
  carrying the MCP message during a tool call (None over stdio), for an HTTP
  `resolve_user`. HTTP is stateless (`stateless_http=True`), so contextvars the API sets
  per request reach `resolve_user`. `transport_security` = SDK Host/Origin checks (None =
  localhost-only; a non-localhost bind must pass its allowed hosts).
  Tools run the service call in a worker thread (blocking I/O) with the caller's context.
  `CteforgeMCP` (FastMCP subclass) maps failures through `error_result`.
- `descriptions.py` — tool descriptions. These are the agent's instructions: when to call
  each tool, the input formats (from VariableCard.input_format), how to read issues and
  trust labels, and that definition text is data, not instructions (DESIGN §14).

If something does not clearly fit one of the above, it does not belong here.
"Not forbidden" is not the same as "allowed."

## Tools (each maps to one service call; responses are the services' card models as JSON)
search · get_definition · compile_and_run · check_and_run · parse_query ·
propose_definition · revise_definition · propose_join · propose_recipe · list_recipes ·
list_joins · describe_tables.
`search` hits carry `match` (keyword | meaning | both) and a `note` when meaning search
could not run; its description explains both (ADR 0023). `get_definition` takes
`include_search_document` (adds the Markdown search document). The tool count stays 12.
`parse_query` and `propose_definition` take an optional `dialect` (omitted → the
connection's: the profile passed, else the team's default connection; services
`_library.resolve_dialect`, ADR 0021).
`revise_definition` takes an optional `new_id`: with nothing else to change it proposes a
rename (ADR 0024; `services.propose.rename_definition`). A former id in any tool fails with
NotFoundError "… was renamed to '<new>' …"; INSTRUCTIONS tells agents to switch to it.
propose_recipe's `recipe` argument is `services.propose_other.RecipeInput` (no `version`,
`state` or `schema_version`: cteforge sets them).
No approve/verify tools (ADR 0007). Errors are returned as tool errors carrying the
CteforgeError message and any Issues, never stack traces or credentials:
text `"<ErrorType>: message"` + `"\nissues: <JSON list>"` when there are issues;
`structuredContent` = `{"error": {"type", "message", "issues"}}` (the HTTP API's shape).
Argument validation → `InvalidArguments` (no input values echoed); any non-cteforge
exception → `InternalError` with a generic message (details only in the server log).
Refusals the services return as outcomes (e.g. check_and_run on DELETE, strict mode) are
normal results with `usable: false` and issues, not tool errors.

## Common misplacements
- Business logic, permission rules — services/ and trust/.

## Interactions
Imports services (and domain for types/errors only). Mounted by api/ for streamable HTTP.

## If you need to add something not on the allowlist
STOP. Do not place the file here on the assumption it kind of fits. Surface the case to
the user, propose the addition, and update this CLAUDE.md (and the root CLAUDE.md if the
change is project-wide) before placing the new code. The architecture evolves through
conscious decisions, not silent drift.
