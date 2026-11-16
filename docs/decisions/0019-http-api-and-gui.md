# 0019 — HTTP API (FastAPI) and GUI (React + Vite) served by it

Status: accepted · 2026-11-16

## Decision
- `api/`: FastAPI, JSON over `/api/v1/...`, thin over services; OpenAPI generated.
- `web/`: React + TypeScript + Vite, no UI framework dependency beyond React; builds to
  `web/dist`, which `cteforge serve` serves at `/` when present. Pages: Library (search +
  list), Definition (SQL, variables, render/run form, history), Import (paste SQL → literal
  table → annotate → checks → propose), Review queue (proposals, diff, approve/reject),
  Audit log, Settings/users (admin).
- One process: `cteforge serve` runs API + MCP (streamable HTTP at `/mcp`) + static GUI.

## Consequences
- The GUI never contains SQL logic; it renders what services return.

## Addendum (2026-11-16)
- Definition page gains an **Edit** tab (contributors, latest version): text-only revisions
  send just the changed metadata keys; "Change SQL or variables" opens Import in revise mode
  (`/import?revise=<id>`), prefilled from `GET /definitions/{id}?include_source=true`
  (`source_sql` + `annotations`) and submitted to `POST /proposals/definitions/{id}/revise`.
- New **Explore** page: Recipes (run with saved values, or open in Compose to change them),
  Compose (several definitions by alias + final SELECT → `/run`; save as a recipe proposal),
  Join map (`GET /joins`), Tables (`GET /tables`), SQL (exploratory, `POST /check`).
- The Review page shows recipe and join proposals as a recipe summary / join table.
