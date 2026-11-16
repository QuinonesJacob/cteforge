# src/cteforge/api/

HTTP API for the GUI and integrations (ADR 0019, 0018). Thin adapter over services.
Project root: ../../../CLAUDE.md

## What belongs here (authoritative)
- `app.py` — `create_app(ctx: AppContext | None = None) -> FastAPI`: routes, error mapping,
  static GUI from `web/dist` at `/` when present, MCP streamable HTTP mounted at `/mcp`.
- `auth.py` — bearer-token dependency → User via services.auth; `CTEFORGE_AUTH=off` only
  when bound to localhost.
- `routes/` — one module per area (definitions, proposals, run, admin, audit).
- `schemas.py` — request bodies only; responses reuse services/cards.py models.

If something does not clearly fit one of the above, it does not belong here.
"Not forbidden" is not the same as "allowed."

## Routes (all under /api/v1, JSON)
GET /me · GET /profiles (name + dialect only) ·
GET /definitions?q=&include_drafts= · GET /definitions/{id}?version= ·
GET /definitions/{id}/history · POST /definitions/{id}/verify {version} ·
POST /parse {sql, dialect, profile?} ·
POST /run {uses? | recipe_id?, values, select?, dialect?, run, limit} ·
POST /check {sql, profile?, run, limit} ·
POST /proposals/definitions {sql, dialect, metadata, annotations, profile?, summary} ·
POST /proposals/definitions/{id}/revise {base_version, sql?, metadata?, annotations?, summary} ·
POST /proposals/joins · POST /proposals/recipes ·
GET /proposals?status= · GET /proposals/{id} · POST /proposals/{id}/approve {verify, comment} ·
POST /proposals/{id}/reject {comment} · POST /proposals/{id}/withdraw ·
GET /recipes · GET /joins?table= · GET /audit?actor=&kind=&definition_id=&limit= ·
GET /users · POST /users {name, role, kind} (returns token once) · POST /users/{name}/token ·
PUT /users/{name}/role · GET /settings · PUT /settings {mode}.
Errors: `{"error": {"type": <ErrorClass>, "message": str, "issues": [Issue]}}` with
PermissionDenied 403 · NotFound 404 · VariableValue/Annotation/Render/SqlParse/Codec 422 ·
StaleVersion/Policy 409 · Warehouse 502 · missing/invalid token 401.

## Common misplacements
- Business logic — services/. SQL in the browser — never.

## Interactions
Imports services, mcp (to mount it), and domain for types/errors only.

## If you need to add something not on the allowlist
STOP. Do not place the file here on the assumption it kind of fits. Surface the case to
the user, propose the addition, and update this CLAUDE.md (and the root CLAUDE.md if the
change is project-wide) before placing the new code. The architecture evolves through
conscious decisions, not silent drift.
