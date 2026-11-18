# src/cteforge/api/

HTTP API for the GUI and integrations (ADR 0019, 0018, 0027). Thin adapter over services.
Every route's permission is the tool of the use-case it calls (services; ADR 0027), so a
token gets the same answer here as over MCP.
Project root: ../../../CLAUDE.md

## What belongs here (authoritative)
- `app.py` — `create_app(ctx: AppContext | None = None, *, host=None, web_dist=None) ->
  FastAPI`: routes, error mapping, static GUI from `web/dist` at `/` when present (SPA
  fallback to index.html; `CTEFORGE_WEB_DIST` overrides; no dist → `/` returns a JSON
  hint), MCP streamable HTTP at `/mcp` (mcp.server `build_server` + `http_app`; its session
  manager runs in this app's lifespan; bearer auth in front; absent module → /mcp 404),
  security headers (CSP `default-src 'self'`, nosniff, no-referrer, DENY; /api no-store),
  no CORS. No ctx → `AppConfig.from_env()` at creation, context opened/closed by the
  lifespan. `host` defaults to `CTEFORGE_BIND_HOST` (set by `cteforge serve`) else
  127.0.0.1. An owned context starts meaning search in the background
  (`search_index.start_background`: requests never wait for the model). Auth off → only a loopback connection (ASGI `client` and `server`, not
  headers; else 403) with a loopback Host/Origin (421/403) is accepted.
- `auth.py` — bearer-token dependency → User via services.auth; `CTEFORGE_AUTH=off` only
  when bound to localhost (`check_bind`, raised by create_app); `CURRENT_USER` contextvar
  set per request (API and /mcp); `request_user` is the MCP server's `resolve_user`.
- `routes/` — one module per area (definitions, proposals, run, admin, audit).
- `schemas.py` — request bodies only; responses reuse services/cards.py models.

If something does not clearly fit one of the above, it does not belong here.
"Not forbidden" is not the same as "allowed."

## Routes (all under /api/v1, JSON)
GET /me → MeCard {name, role, kind, active, created_at, tools, review_all, role_allowed,
role_defaults, token_id, token_expires_at, token_mode (the session's token; null without)} · GET /tools → ToolInfo[] {name, group, admin_only} · GET /profiles (name, dialect, `default` = the effective default connection; only these) ·
GET /library?q=&trust=&approval=&created_by=&verified_by=&needs_my_review= → LibraryPage
(ADR 0026; tool `search`; proposal rows/badges only with list_proposals; needs_my_review
needs review_proposal) ·
GET /definitions?q=&include_drafts=&limit= · GET /definitions/{id}?version=&include_source= (adds `source_sql` + `annotations`, for revising; card has composed, composition, used_by, joins) ·
GET /definitions/{id}/history · GET /definitions/{id}/references → DefinitionReferences
(used_by, pending proposals, former_ids, outside_warning; ADR 0024, 0026) ·
GET /definitions/{id}/search-document?version= →
SearchDocument {id, version, markdown} (ADR 0023) · POST /definitions/{id}/verify {version} ·
POST /parse {sql, dialect?, profile?} (dialect omitted → the profile's, else the default connection's; ADR 0021) ·
POST /run {uses, values, select?, dialect?, profile?, run, limit, question?} ·
POST /check {sql, profile?, run, limit, question?} · POST /revalidate {ids?} ·
POST /proposals/definitions {sql | composition, dialect?, metadata, annotations?, profile?, summary} ·
POST /proposals/definitions/{id}/revise {base_version, sql? | composition?, metadata?, annotations?, summary} ·
POST /proposals/definitions/{id}/rename {new_id, base_version?, summary} (ADR 0024) ·
POST /proposals/joins {action: annotate (default) | add, id?, left_table?, right_table?,
keys?, join_type?, relationship?, traps?, notes?, summary} ·
GET /proposals?status= · GET /proposals/count?status= (cheap, not audited; for the nav badge) · GET /proposals/{id} · POST /proposals/{id}/approve {verify, comment} ·
POST /proposals/{id}/reject {comment} · POST /proposals/{id}/withdraw ·
GET /joins?table=&known_only=&definition= → JoinCard[] ·
GET /tables?name=&name=&profile= · GET /audit?actor=&kind=&definition_id=&limit= ·
GET /users → UserView[] (role, kind, active, tokens' metadata — never values) ·
POST /users {name, role, kind} → 201 {user, token | null} (a "default" token, once, only
to a human caller) · PATCH /users/{name} {role?, active?} → UserView ·
GET /users/{name}/tokens · POST /users/{name}/tokens · PUT /users/{name}/tokens/{id}
{tool_mode, include, exclude} · DELETE /users/{name}/tokens/{id} (revoke → TokenCard) ·
GET /me/tokens · POST /me/tokens · PUT /me/tokens/{id} · DELETE /me/tokens/{id} — token
POST bodies {name, expires_at?, tool_mode: follow_role | only, include, exclude} → 201
NewToken {token, info}: the ONLY responses that ever carry a token value (human callers
only; agent callers → 403) · GET /roles → RoleCard[] · POST /roles {name, description,
allowed, defaults?, review_all} → 201 · PUT /roles/{name} {description?, allowed?,
defaults?, review_all?} · DELETE /roles/{name} → 204 · GET /settings → TeamSettings {mode, default_connection,
effective_default_connection, issues} · PUT /settings {mode?, default_connection?: str | null}
(admin; only the fields given change, at least one; `default_connection` first — an unknown
profile → 404 — then `mode`, each its own audited service call).
/proposals/count
→ {count}. GET /joins and /tables call services/catalog.py; GET /library services/library.py.
Errors: `{"error": {"type": <ErrorClass>, "message": str, "issues": [Issue]}}` with
PermissionDenied 403 · NotFound 404 · VariableValue/Annotation/Render/SqlParse/Codec 422 ·
StaleVersion/Policy 409 · Warehouse 502 · missing/invalid token 401 (WWW-Authenticate) ·
other Store 409 · Audit 503 · request validation 422 (`RequestValidationError`) · unknown
routes 404 (`HTTPError`) · anything else 500 `InternalError` "internal error" (logged as
class + frame locations only, never the message).

## Common misplacements
- Business logic — services/. SQL in the browser — never.

## Interactions
Imports services, mcp (to mount it), and domain for types/errors only.

## If you need to add something not on the allowlist
STOP. Do not place the file here on the assumption it kind of fits. Surface the case to
the user, propose the addition, and update this CLAUDE.md (and the root CLAUDE.md if the
change is project-wide) before placing the new code. The architecture evolves through
conscious decisions, not silent drift.
