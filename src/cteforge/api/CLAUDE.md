# src/cteforge/api/

HTTP API for the GUI and integrations (ADR 0019, 0018). Thin adapter over services.
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
  127.0.0.1. Auth off → only a loopback connection (ASGI `client` and `server`, not
  headers; else 403) with a loopback Host/Origin (421/403) is accepted.
- `auth.py` — bearer-token dependency → User via services.auth; `CTEFORGE_AUTH=off` only
  when bound to localhost (`check_bind`, raised by create_app); `CURRENT_USER` contextvar
  set per request (API and /mcp); `request_user` is the MCP server's `resolve_user`.
- `routes/` — one module per area (definitions, proposals, run, admin, audit).
- `schemas.py` — request bodies only; responses reuse services/cards.py models.

If something does not clearly fit one of the above, it does not belong here.
"Not forbidden" is not the same as "allowed."

## Routes (all under /api/v1, JSON)
GET /me · GET /profiles (name + dialect only) ·
GET /definitions?q=&include_drafts=&limit= · GET /definitions/{id}?version= ·
GET /definitions/{id}/history · POST /definitions/{id}/verify {version} ·
POST /parse {sql, dialect, profile?} ·
POST /run {uses? | recipe_id?, values, select?, dialect?, profile?, run, limit, question?} ·
POST /check {sql, profile?, run, limit, question?} · POST /revalidate {ids?} ·
POST /proposals/definitions {sql, dialect, metadata, annotations, profile?, summary} ·
POST /proposals/definitions/{id}/revise {base_version, sql?, metadata?, annotations?, summary} ·
POST /proposals/joins · POST /proposals/recipes ·
GET /proposals?status= · GET /proposals/count?status= (cheap, not audited; for the nav badge) · GET /proposals/{id} · POST /proposals/{id}/approve {verify, comment} ·
POST /proposals/{id}/reject {comment} · POST /proposals/{id}/withdraw ·
GET /recipes?q=&include_drafts= · GET /joins?table=&include_drafts= ·
GET /tables?name=&name=&profile= · GET /audit?actor=&kind=&definition_id=&limit= ·
GET /users · POST /users {name, role, kind} (returns token once) · POST /users/{name}/token ·
PUT /users/{name}/role · GET /settings · PUT /settings {mode}.
POST /users → 201 {user, token}; POST /users/{name}/token → {name, token}; /proposals/count
→ {count}. GET /recipes, /joins and /tables call services/catalog.py.
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
