# cteforge

**Paste the SQL you already trust. No semantic model required.**

cteforge is an open-source server (web GUI + MCP + HTTP API) that turns a team's trusted SQL
into a library AI agents can search, reuse, change safely, and build on. A trusted query is
kept as written; only the parts that change (a date range, a list of regions, a threshold)
are described as **typed variables**, and the analyst decides which ones agents may change.
Agents call definitions *by reference* and pick values; cteforge validates them, writes the
SQL from AST nodes, proves on import that the template gives the same query back, runs it,
and labels every result with how far it can be trusted. Verified logic never passes back
through the LLM.

Semantic layers (dbt Semantic Layer, Cube, WrenAI MDL) ask you to model tables, joins and
metrics first. cteforge starts from the 200-line CTE your finance team already signed off
on and describes only its holes; a semantic model emerges bottom-up from there.

> Status: pre-alpha. See [docs/DESIGN.md](docs/DESIGN.md) (what and why) and
> [docs/decisions/](docs/decisions/) (one ADR per decision). Limitations are listed
> [below](#status-and-limitations).

## Quickstart

Requires [uv](https://docs.astral.sh/uv/), Python 3.12, and Node 20.19+ (Vite 7) to build the GUI.

```bash
uv sync
(cd web && npm ci && npm run build)      # the GUI, served by `cteforge serve`

uv run cteforge demo                      # → ./cteforge-demo
```

`cteforge demo` seeds a synthetic DuckDB warehouse, creates the library database, a human
admin (`admin`) and an agent user (`claude-agent`), and imports three example definitions
from `examples/`. It prints both tokens **once** (only hashes are stored) and writes
`cteforge-demo/env.sh` with the settings (no secrets).

```bash
source cteforge-demo/env.sh
uv run cteforge search realization
uv run cteforge run --use rr=realization_rate \
  --vars '{"rr": {"period": {"start": "2025-07-01", "end": "2025-10-01"}, "regions": ["APAC"]}}'
uv run cteforge serve                     # GUI + API + MCP on http://127.0.0.1:8765
```

Open <http://127.0.0.1:8765> and sign in with the admin token. The GUI has the Library,
a run form per definition, Import (paste SQL, mark the literals that are variables, check,
propose), the Review queue, the Audit log and Settings (users, tokens, team mode).

### Connect Claude Code (or any MCP client)

**stdio** (local; acts as `CTEFORGE_USER`, which must already exist: stdio never creates
users, so an agent can't become the first admin of an empty database; `cteforge demo`
creates `claude-agent`). The demo prints a ready `.mcp.json` block; or:

```bash
source cteforge-demo/env.sh
claude mcp add cteforge \
  -e CTEFORGE_DB_URL="$CTEFORGE_DB_URL" \
  -e CTEFORGE_CONNECTIONS="$CTEFORGE_CONNECTIONS" \
  -e CTEFORGE_DEFAULT_PROFILE=demo \
  -e CTEFORGE_USER=claude-agent \
  -- uv run --directory "$PWD" cteforge mcp
```

**Streamable HTTP** (against `cteforge serve`; acts as the token's user):

```bash
claude mcp add --transport http cteforge http://127.0.0.1:8765/mcp \
  --header "Authorization: Bearer <agent token>"
```

Then ask: *"What was the realization rate in APAC in Q3 2025?"* The agent calls `search`,
`get_definition`, then `compile_and_run` with `{"period": {"start": "2025-07-01", "end":
"2025-10-01"}, "regions": ["APAC"]}` and reports the trust label with the numbers.

MCP tools: `search`, `get_definition`, `compile_and_run`, `check_and_run` (exploratory SQL,
labeled), `parse_query`, `propose_definition`, `revise_definition`, `propose_join`,
`propose_recipe`, and for discovery `list_recipes`, `list_joins` and `describe_tables`. There
is no approve or verify tool: people do that (GUI, API, CLI).

## Concepts

- **Definition** — one trusted query plus its meaning, keywords, "use when / don't use when",
  grain, outputs, tables, join keys, owner and version history.
- **Variables** — typed holes in the SQL. Each literal is left alone, made **fixed** (named
  and visible to agents; overriding it warns and lowers trust) or **changeable**. Nothing is
  changeable by default (ADR 0015). One variable can fill several places (the same period in
  two CTEs).
- **Typed inputs** — one strict input format per type; the SQL decides how the value is
  written (casts, `strptime` formats, arithmetic around it stay as written; ADR 0014):

  | Type | Input |
  |---|---|
  | `date_range` | `{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}` (shape: half-open, inclusive, …) |
  | `date` | `"YYYY-MM-DD"` |
  | `text_list` / `text` | `["a", "b"]` / `"a"` |
  | `number` / `boolean` | `123`, `1.5` / `true` |
  | `grain` | `"day" \| "week" \| "month" \| "quarter" \| "year"` |

  `"2026-Q3"` is rejected with the expected format; the agent translates meaning into values.
- **Trust labels** on every result, best to worst: `verified` · `built_from_verified_parts` ·
  `llm_annotated` · `new_logic_on_known_joins` · `exploratory`. Overrides of fixed
  variables, values outside `allowed`, and cross-dialect renders each lower it one step.
- **Proposals and review** — every change (new or revised definition, recipe, join) is a
  proposal, checked on the warehouse: same query back (AST and results), types, EXPLAIN.
  In **open** mode a passing proposal for a new or unverified definition applies at once as
  `llm_annotated`; changes to a **verified** definition always wait for a person. **Strict**
  mode only runs verified definitions.
- **Four-eyes** — nobody approves their own proposal or verifies a version they authored,
  unless they are an admin (so single-user local mode works). Agents never verify,
  whatever their role (ADR 0007, ADR 0018).
- **Join map and recipes** — joins are mined from trusted SQL (also through CTEs) and used to
  label new SQL; recipes save definitions + values + a final SELECT by reference.
- **Audit** — cteforge logs its own calls (actor, definitions + versions, values, label, row
  count, SQL hash — never SQL text or rows) and tags every query
  `/* cteforge: realization_rate v3 compile_id=… */` so it joins with the warehouse's logs.

## CLI reference

All commands act as `CTEFORGE_USER` against `CTEFORGE_DB_URL`. Run `cteforge COMMAND --help`.

| | |
|---|---|
| `demo [DIR]` | Seed the demo warehouse, users and example definitions |
| `serve [--host --port]` | GUI at `/`, HTTP API at `/api/v1`, MCP at `/mcp` |
| `mcp` | MCP over stdio |
| `search QUERY` · `run --use ALIAS=ID --vars JSON [--select SQL]` | Use the library |
| `db init` | Create the database |
| `user add NAME [--role R] [--agent] [--bootstrap]` · `user list\|role\|token` | Users and tokens (token printed once) |
| `settings show` · `settings mode strict\|open` | Team mode |
| `proposals list [--status pending]` · `show` · `approve [--verify] [--comment]` · `reject --comment` | Review |
| `verify ID VERSION` · `revalidate [IDS]` | Verify a version; drift check against the warehouse |
| `export DIR` · `import-library DIR` | The library as YAML files (git optional) |
| `parse` · `import` · `render` | File-based tools from M1 (no server) |

Environment: `CTEFORGE_DB_URL` (default `sqlite:///cteforge.db`; Postgres via SQLAlchemy URL),
`CTEFORGE_CONNECTIONS` (path to connections.yml), `CTEFORGE_DEFAULT_PROFILE`,
`CTEFORGE_AUTH` (`on`; `off` makes every HTTP call the local admin and is refused unless bound
to localhost), `CTEFORGE_USER` (stdio MCP and CLI).

## Warehouse profiles

Profiles live in a `connections.yml` (see
[examples/connections.example.yml](examples/connections.example.yml)). Secrets are **never**
written there: `secrets_env` names the environment variable that holds each one, resolved
only at connect time and never logged or returned (ADR 0020). Drivers other than DuckDB are
extras: `pip install 'cteforge[postgres]'` (also Redshift), `[snowflake]`, `[bigquery]`,
`[databricks]`.

```yaml
profiles:
  analytics_pg:
    dialect: postgres
    options: {host: db.internal, port: 5432, dbname: analytics, user: cteforge_ro}
    secrets_env: {password: PG_PASSWORD}
```

**Always connect as a dedicated read-only role.** cteforge refuses non-SELECT SQL, enforces
row limits and uses read-only sessions where the warehouse has them, but grants are the real
guarantee:

| Warehouse | Read-only setup |
|---|---|
| DuckDB | Opened read-only with external access disabled; one SELECT per call. No setup. |
| Postgres / Redshift | Every transaction is `BEGIN READ ONLY` and rolled back. Still use a role with only `USAGE` + `SELECT` (`CREATE ROLE cteforge_ro LOGIN; GRANT USAGE ON SCHEMA s TO cteforge_ro; GRANT SELECT ON ALL TABLES IN SCHEMA s TO cteforge_ro;`). |
| Snowflake | No read-only session mode: set `options.role` to a role with only USAGE on warehouse/database/schemas and SELECT on tables. One statement per call. |
| BigQuery | Service account with `roles/bigquery.dataViewer` (datasets) + `roles/bigquery.jobUser` (project). Every job sets `maximum_bytes_billed` (default 10 GiB); EXPLAIN is a dry run. |
| Databricks | No read-only session mode: a service principal with only `USE CATALOG`, `USE SCHEMA`, `SELECT` and CAN USE on the SQL warehouse. |

## Architecture

```
  Browser GUI (web/, React)    Claude Code / MCP clients        CLI
        │  /api/v1                │ stdio · streamable HTTP /mcp    │
        ▼                         ▼                                 ▼
  ┌──────────── entry points: api/ · mcp/ · cli/ (thin, no logic) ───────────┐
  └──────────────────────────────────┬───────────────────────────────────────┘
                                     ▼
              services/  use-cases: permissions, audit, orchestration
          ┌──────────────────────────┴──────────────────────────────┐
  pure core                                               I/O adapters
  domain ← parsing ← templating ← {compose, trust}        warehouse/ (hardened) · store/
  (sqlglot only in parsing/templating/compose)            audit/ · files/
```

Layering is enforced by import-linter (`uv run lint-imports`). Each directory has a
`CLAUDE.md` saying what belongs there; start at [CLAUDE.md](CLAUDE.md). Design:
[docs/DESIGN.md](docs/DESIGN.md). Key decisions:
[0002 trusted SQL, not a semantic model](docs/decisions/0002-trusted-sql-not-semantic-model.md) ·
[0005 the LLM picks values, the tool renders](docs/decisions/0005-llm-picks-values-tool-renders.md) ·
[0007 trust states](docs/decisions/0007-trust-states.md) ·
[0014 the SQL determines the format](docs/decisions/0014-sql-determines-format.md) ·
[0015 the analyst decides](docs/decisions/0015-analyst-decides-tool-reports-facts.md) ·
[0018 MCP and auth](docs/decisions/0018-mcp-and-auth.md) ·
[0019 HTTP API and GUI](docs/decisions/0019-http-api-and-gui.md) ·
[0020 warehouse connectors](docs/decisions/0020-warehouse-connectors.md).

## Development

```bash
uv sync
uv run pytest                              # all Python tests (incl. tests/e2e)
uv run pytest -m "not e2e"                 # skip the real-server end-to-end tests
uv run lint-imports                        # layering contracts
uv run ruff check . && uv run ruff format --check .
uv run python examples/seed_warehouse.py   # examples/warehouse.duckdb

cd web
npm ci
npm run dev        # Vite on :5173, proxies /api and /mcp to `cteforge serve` on :8765
npm run typecheck && npm test && npm run build
```

`tests/e2e/test_server_e2e.py` runs `cteforge demo`, starts `cteforge serve`, loads the built
GUI, drives the API through a four-eyes review, and talks MCP over streamable HTTP and stdio;
it is skipped when `web/dist` is not built. `tests/e2e/test_gui_contract.py` pins every API
call the GUI makes.

## Status and limitations

- Pre-alpha: APIs, the database schema and the file format may change.
- Only **DuckDB** is tested against a real warehouse. The Postgres/Redshift, Snowflake,
  BigQuery and Databricks connectors are unit-tested against fake drivers and still need
  validation on real warehouses. The library store is tested on SQLite; Postgres is untested
  in CI.
- Cross-dialect rendering uses sqlglot (pinned at 30.31.0) and is labeled and lowers trust.
  Known transpile defects (kept visible in `tests/golden/`): Postgres has no
  `ROUND(double precision, int)`; BigQuery gets `TIMESTAMP_TRUNC` / `PARSE_TIMESTAMP` on
  `DATETIME` columns; Postgres `TO_TIMESTAMP` is zone-aware against naive columns. Week
  starts differ by warehouse (DESIGN §13). Prefer rendering in the source dialect.
- The HTTP API does not yet serve `GET /recipes` or `GET /joins`; recipes and joins are
  proposed and used through MCP, the API and the CLI.
- `cteforge demo` needs a source checkout (it reads `examples/`).

## License

Apache-2.0
