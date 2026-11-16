# src/cteforge/services/

Use-cases. Orchestrates the pure core and the I/O adapters. The ONLY layer that does I/O
(through adapters). Project root: ../../../CLAUDE.md

## What belongs here (authoritative)
Support modules (shared by use-cases; not use-cases themselves):
- `context.py` — `AppConfig` (from env: `CTEFORGE_DB_URL` default `sqlite:///cteforge.db`,
  `CTEFORGE_CONNECTIONS` path to connections.yml, `CTEFORGE_DEFAULT_PROFILE`,
  `CTEFORGE_AUTH` on|off, `CTEFORGE_USER` for stdio/local) and `AppContext` (store, audit
  log, warehouse registry, config). `open_context(config) -> AppContext`; `close()`.
- `connections.py` — warehouse registry: profile name → lazily opened `Warehouse`
  (dispatch on dialect to warehouse/ connectors); `open_warehouse(path)` kept for the M1 CLI.
- `auth.py` — token → User (SHA-256 lookup), `require(user, action)` via trust.approval,
  local-mode user, token generation (`secrets.token_urlsafe`), shown once.
- `cards.py` — response models shared by use-cases and adapters: VariableCard (name, type,
  role, input_format, description, allowed, unit, column, shape, original_value),
  DefinitionCard, SearchHit, RunOutcome, ParseReport, ProposalOutcome, ProposalView, …
- `_audit.py` — helper that records an AuditEvent for a use-case call.
Use-cases (one file each; a use-case never imports another use-case — share via support
modules or the core):
- `search.py` — `search(ctx, user, query, *, limit=10, include_drafts=False, question=None) -> list[SearchHit]` (DESIGN §19, ADR 0017).
- `describe.py` — `get_definition(ctx, user, id, version=None) -> DefinitionCard`; `definition_history(...)`.
- `parse_query.py` — `parse_query(ctx, user, sql, dialect, profile=None) -> ParseReport`.
- `import_query.py` — checks engine for a trusted query + annotations (DESIGN §7) and the M1 file-based import.
- `render_definition.py` — M1 file-based render/run (CLI).
- `propose.py` — `propose_definition(...)`, `revise_definition(...)` → ProposalOutcome (DESIGN §18).
- `propose_other.py` — `propose_join(...)`, `propose_recipe(...)`.
- `review.py` — list/get proposals (with diff + impact preview), approve (optionally verify), reject, withdraw, `verify_definition`.
- `run.py` — `compile_and_run(...)`, `check_and_run(...)` → RunOutcome (render → compose → trust → explain → run → audit; SQL tagged per ADR 0008).
- `revalidate.py` — drift check over latest versions (DESIGN §10, §18).
- `admin.py` — users, roles, tokens, settings (team mode).
- `audit_log.py` — list audit events.
- `library_io.py` — export/import the library as YAML files (git optional, ADR 0003) via files/.

Every use-case: takes `ctx` and the acting `user` first; checks permissions
(`auth.require`); records an audit event; raises CteforgeError subclasses or returns Issues.

If something does not clearly fit one of the above, it does not belong here.
"Not forbidden" is not the same as "allowed."

## Common misplacements
- SQL manipulation — parsing/, templating/, compose/. Label/approval policy — trust/.
- Argument parsing, printing, HTTP/MCP schemas — cli/, mcp/, api/ (they reuse cards.py models).

## Interactions
Imports everything in the core and adapters. Imported only by cli, mcp, api.

## If you need to add something not on the allowlist
STOP. Do not place the file here on the assumption it kind of fits. Surface the case to
the user, propose the addition, and update this CLAUDE.md (and the root CLAUDE.md if the
change is project-wide) before placing the new code. The architecture evolves through
conscious decisions, not silent drift.
