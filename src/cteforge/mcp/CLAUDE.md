# src/cteforge/mcp/

MCP server (ADR 0018, 0027, DESIGN §6). Thin adapter over services. Project root: ../../../CLAUDE.md

## What belongs here (authoritative)
- `server.py` — `build_server(ctx: AppContext, resolve_user: Callable[[], User], *,
  transport_security=None) -> FastMCP` registering the tools below; `run_stdio()` used by
  `cteforge mcp` (acts via `services.auth.stdio_user`: CTEFORGE_TOKEN's user and tools when
  set, else CTEFORGE_USER with its role's defaults; re-resolved per call, so revocation
  applies at once; the user must exist, an empty store is refused; starts meaning search in the
  background — `search_index.start_background` — so no tool call waits for the model);
  `http_app(server, *, path="/") -> Starlette` for the API to mount (mounted at `/mcp` →
  endpoint `/mcp/`; the parent app's lifespan must run `server.session_manager.run()`,
  Starlette does not run a mounted app's lifespan); `current_request()` — the HTTP request
  carrying the MCP message during a tool call (None over stdio), for an HTTP
  `resolve_user`. HTTP is stateless (`stateless_http=True`), so contextvars the API sets
  per request reach `resolve_user`. `transport_security` = SDK Host/Origin checks (None =
  localhost-only; a non-localhost bind must pass its allowed hosts).
  Tools run the service call in a worker thread (blocking I/O) with the caller's context.
  `CteforgeMCP` (FastMCP subclass) maps failures through `error_result`, overrides
  `list_tools` to show only the caller's tools (`MCP_TOOL_ACCESS` / `visible_tools` over
  `services.auth.user_tools`; no or failing caller → no tools) and `call_tool` to refuse a
  hidden tool before it runs (`auth.refuse_tool`: PermissionDeniedError, audited). Result
  models for the multi-action tools live here: ReviewResult, AccessOverview,
  ManageUserResult, ManageRoleResult.
- `descriptions.py` — tool descriptions. These are the agent's instructions: when to call
  each tool, the input formats (from VariableCard.input_format), how to read issues and
  trust labels, and that definition text is data, not instructions (DESIGN §14).

If something does not clearly fit one of the above, it does not belong here.
"Not forbidden" is not the same as "allowed."

## Tools (each maps to one service call; responses are the services' card models as JSON)
search · get_definition · compile_and_run · check_and_run · parse_query ·
propose_definition · revise_definition · propose_join · propose_recipe · list_recipes ·
list_joins · describe_tables · list_proposals · review_proposal · list_users ·
manage_user · manage_role (17; ADR 0027). Each is shown only to callers holding the tool
of the same name (domain/access.py); `review_proposal` also to callers holding `verify`.
Annotations: reads `readOnlyHint: true`; proposals `readOnlyHint: false`,
`destructiveHint: false`; review_proposal / manage_user / manage_role
`destructiveHint: true`.
- `compile_and_run` with a `select` that reads raw tables needs `check_and_run` too
  (services/run.py; refused otherwise); its description says so.
- `list_proposals(status=pending)` → ProposalView list. `review_proposal(action: approve |
  reject | verify, id?, comment, definition_id?, version?)` → ReviewResult (approve → the
  ProposalOutcome; reject → the ProposalView (comment required); verify with `id` →
  approve(verify=True), with `definition_id` + `version` → verify_definition).
- `list_users()` → AccessOverview {users: UserView (token metadata, never values),
  roles: RoleCard}. `manage_user(action: create | set_role | set_tools | deactivate |
  activate | revoke_token, name, role?, kind=agent, token_id?, tool_mode?, include?,
  exclude?)` → ManageUserResult (create never returns a token: `issue_token=False`).
  `manage_role(action: create | edit | delete, name, description?, allowed?, defaults?,
  review_all?)` → ManageRoleResult. No tool ever returns a token.
`search` hits carry `match` (keyword | meaning | both) and a `note` when meaning search
could not run; its description explains both (ADR 0023). `get_definition` takes
`include_search_document` (adds the Markdown search document).
`parse_query` and `propose_definition` take an optional `dialect` (omitted → the
connection's: the profile passed, else the team's default connection; services
`_library.resolve_dialect`, ADR 0021).
`revise_definition` takes an optional `new_id`: with nothing else to change it proposes a
rename (ADR 0024; `services.propose.rename_definition`). A former id in any tool fails with
NotFoundError "… was renamed to '<new>' …"; INSTRUCTIONS tells agents to switch to it.
propose_recipe's `recipe` argument is `services.propose_other.RecipeInput` (no `version`,
`state` or `schema_version`: cteforge sets them).
Approve/verify only through `review_proposal`, under the services' rules (four-eyes with
no agent exemption; verify and changes to verified work need a human or the admin role).
Errors are returned as tool errors carrying the
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
