# src/cteforge/cli/

Typer CLI: the local admin tool. Thin adapter over services. Project root: ../../../CLAUDE.md

## What belongs here (authoritative)
- `_common.py` — the shared Typer `app`; `context()` (open_context from CTEFORGE_* env,
  closed after) and `session()` (context + acting user = `services.auth.local_user`,
  i.e. CTEFORGE_USER); exits (`fail` → 1, `usage` → 2); `--vars`-style JSON parsing
  (decimals exact); printing helpers (issues, tables, JSON, tokens, proposals, runs).
- `main.py` — console-script entry (`cteforge.cli.main:app`); imports the modules below so
  they register; keeps the M1 file-based commands (no store, no user):
  - `parse SQL_FILE [-d dialect] [-w warehouse.duckdb] [--json]` — literal IDs, compared
    types, context facts, wrappers, suggested formats, range candidates, joins.
  - `import SQL_FILE ANNOTATIONS [-d] [-w] [-o out.yml]` — same-query-back checks; prints
    each issue as `<severity>: <code> [variable] [literal]: message`; exit 1 on any error
    issue; writes the file only when no issue is an error.
  - `render DEFINITION [--vars JSON] [-d target] [-w] [--run] [--limit]` — issues go to
    stderr; `--vars` must be a JSON object (decimals parsed exactly).
- `admin.py` — services.admin: `db init` (creates schemas; lists profiles) ·
  `user add NAME --role R [--agent]` / `user add NAME --bootstrap [--agent]` (first admin,
  no acting user) · `user list [--json]` · `user role NAME ROLE` · `user token NAME` ·
  `settings show [--json]` (mode, default connection, effective default, warnings on
  stderr) · `settings mode strict|open` · `settings connection [NAME] [--clear]` (the team's
  default connection, ADR 0021; no argument shows it; NAME with --clear → exit 2; unknown
  profile → exit 1). Tokens: printed once on stdout,
  the warning on stderr; never written anywhere.
- `review.py` — services.review / revalidate: `proposals list [--status] [--json]` ·
  `proposals show ID [--json]` (checks, impact, diff) · `proposals approve ID [--verify]
  [--comment]` · `proposals reject ID --comment` (required, exit 2 without) ·
  `verify ID VERSION` · `revalidate [IDS...] [--json]` (exit 1 when anything drifted).
- `library.py` — services.search / search_index / run / describe / propose / library_io: `search QUERY [-n]
  [--drafts] [--json]` (MATCH column: keyword | meaning | both; a hit `note` is printed
  once on stderr as `note: …`) · `search-index rebuild [--force] [--json]` (admin) ·
  `search-index download-model` (into CTEFORGE_EMBED_CACHE; no store, no user) · `run --use alias=id[@v] ... [--vars JSON] [--select SQL] [--profile]
  [--dialect] [--limit] [--compile-only] [--sql] [--question] [--json]` (`--vars` is
  alias → variable → value; exit 1 when refused or any error issue) · `references ID
  [--json]` (recipes, pending proposals, former ids + the outside-cteforge warning; ADR
  0024) · `rename OLD NEW [--summary] [--json]` (proposes the rename; same policy as any
  change: applied at once only in open mode on a non-verified definition) · `export DIR
  [--prune]` · `import-library DIR [--profile] [--json]`.
- `server.py` — `serve [--host 127.0.0.1] [--port 8765] [--reload]`: uvicorn with the
  factory string "cteforge.api.app:create_app" (factory=True); refuses (exit 2) a
  non-loopback host when CTEFORGE_AUTH=off; exports the bind host as CTEFORGE_BIND_HOST for
  the API's own check. `mcp`: `cteforge.mcp.server.run_stdio()` loaded with importlib at
  call time; prints nothing on stdout (it is the MCP channel).
- `demo.py` — `demo [DIR=./cteforge-demo] [--admin admin] [--agent claude-agent]
  [--examples DIR] [--force]`: seeds DIR/warehouse.duckdb by running
  examples/seed_warehouse.py by path (runpy), writes DIR/connections.yml (profile `demo`)
  and DIR/env.sh (CTEFORGE_* exports, shell-quoted, no tokens), bootstraps the admin,
  creates the agent (contributor), proposes the example queries + annotation files through
  services.propose (auto-applied in open mode; `DEMO_JOIN_KEYS` adds join_keys such as
  month/region unless the annotation file declares its own, so composing two demo
  definitions keeps their trust label), prints both tokens once and next steps
  (incl. a Claude Code `mcpServers` snippet acting as the agent). Refuses an existing
  DIR/cteforge.db unless --force. Needs a source checkout (examples/ next to src/).

Database URLs are printed only as `AppConfig.display_db_url` (password redacted; `serve`,
`db init`, `demo`).

Exit codes: 0 ok, 1 cteforge error / refusal / error issues, 2 bad command-line input.
Errors print `error: <message>` (and any Issues) on stderr, never a traceback.
Argument parsing and output formatting only.

If something does not clearly fit one of the above, it does not belong here.
"Not forbidden" is not the same as "allowed."

## Common misplacements
- Any logic beyond calling a service and printing the result — services/.
- Importing api/ or mcp/ statically — load them by name at call time (see server.py).
- File I/O for the library (definitions, recipes, joins) — services.library_io / files/.

## Interactions
Imports services (and domain for types/errors only). Tests: tests/unit/cli/ (CliRunner,
CTEFORGE_* env on tmp dirs; the demo tests seed a DuckDB warehouse in a tmp dir).

## If you need to add something not on the allowlist
STOP. Do not place the file here on the assumption it kind of fits. Surface the case to
the user, propose the addition, and update this CLAUDE.md (and the root CLAUDE.md if the
change is project-wide) before placing the new code. The architecture evolves through
conscious decisions, not silent drift.
