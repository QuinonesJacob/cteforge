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
