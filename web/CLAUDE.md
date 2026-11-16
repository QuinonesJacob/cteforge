# web/

React + TypeScript + Vite GUI (ADR 0019). Talks only to the HTTP API (`/api/v1`, see
src/cteforge/api/CLAUDE.md). Builds to `web/dist`, served by `cteforge serve`.
Project root: ../CLAUDE.md

## What belongs here (authoritative)
- `package.json`, `vite.config.ts`, `tsconfig*.json`, `index.html`.
- `src/api/` — typed API client (types mirror services/cards.py), token handling.
- `src/pages/` — Library, Definition, Import, Review, Audit, Settings.
- `src/components/` — shared UI pieces (SQL viewer, variable form, issue list, trust badge).
- `src/styles/` — plain CSS with light/dark tokens.

If something does not clearly fit one of the above, it does not belong here.
"Not forbidden" is not the same as "allowed."

## Rules
- No SQL logic in the browser: the GUI shows what the API returns and sends values.
- Token kept in sessionStorage; never logged. Dev server proxies /api to localhost:8765.
- Dependencies: react, react-dom, react-router-dom, vite, typescript, @vitejs/plugin-react,
  vitest. Nothing else without a reason in this file.

## Common misplacements
- Any SQL logic — the backend owns it; the GUI calls the API.

## If you need to add something not on the allowlist
STOP. Do not place the file here on the assumption it kind of fits. Surface the case to
the user, propose the addition, and update this CLAUDE.md (and the root CLAUDE.md if the
change is project-wide) before placing the new code. The architecture evolves through
conscious decisions, not silent drift.
