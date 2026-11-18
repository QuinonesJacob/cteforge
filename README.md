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

Open <http://127.0.0.1:8765> and sign in with the admin token. The GUI has five pages:
**Library** (every definition and the proposals touching it on one page — filter by trust,
approval, created by and verified by, or "needs my review"; each definition has a page with
a run form, "joins to", "used by", its history, an Edit tab, and for a composed definition
its parts and full compiled SQL; proposals open with their diff and checks), **Join map**
(edges mined from the definitions with relationship, traps, notes and where they come
from; propose annotations or new edges), **Import** (paste SQL, mark the literals that are
variables, check, propose), **Audit** and **Settings** (users, roles, my tokens, team mode).

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
labeled), `parse_query`, `propose_definition` (trusted SQL, or a composition of existing
definitions), `revise_definition`, `propose_join` (annotate an edge or add one), and for
discovery `list_joins` and `describe_tables`; when
the token allows them, `list_proposals` / `review_proposal` (approve, reject, verify — never
one's own work) and the admin tools `list_users`, `manage_user`, `manage_role`. Each client
sees only the tools its token allows (ADR 0027). stdio acts as `CTEFORGE_TOKEN`'s user and
tools when it is set, else as `CTEFORGE_USER` with its role's default tools.

## Search

`search` (MCP, GUI, `cteforge search`) matches **words** (names, keywords, tables, IDs) and
**meaning** (a question asked in other words), merged into one ranking; each hit says
whether it matched by `keyword`, `meaning` or `both` (ADR 0023). Meaning search runs inside
the server with a small embedding model (`BAAI/bge-small-en-v1.5` via fastembed, CPU only,
nothing sent elsewhere); vectors are stored in cteforge's own database.

- The first search downloads the model once (about 65 MB) into `CTEFORGE_EMBED_CACHE`
  (default `~/.cache/cteforge/models`). `cteforge serve` and `cteforge mcp` load it in the
  background and answer by keyword until it is ready.
- Servers without internet: run `cteforge search-index download-model` where there is
  internet, then copy that directory to the server and point `CTEFORGE_EMBED_CACHE` at it.
- `CTEFORGE_SEARCH_EMBEDDINGS=off` turns meaning search off (keyword search only). If the
  model can't load, search falls back to keywords and says so in a note.
- `cteforge search-index rebuild [--force]` (admin) re-embeds the library, e.g. after
  changing models; normally vectors are kept up to date automatically.

## Concepts

- **Definition** — one trusted query plus its meaning, keywords, "use when / don't use when",
  grain, outputs, tables, join keys, owner and version history. Its body is trusted SQL, or
  a **composition** of other definitions (ADR 0026): parts by reference + a final SELECT +
  values it always sets. Parts float on their latest usable version (or are pinned), so
  fixing a definition fixes everything built on it; agents try a combination with
  `compile_and_run`, then propose it.
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
- **Proposals and review** — every change (new or revised definition, join annotation or new
  edge, rename) is a
  proposal, checked on the warehouse: same query back (AST and results), types, EXPLAIN.
  In **open** mode a passing proposal for a new or unverified definition applies at once as
  `llm_annotated`; changes to a **verified** definition always wait for a person (or an
agent holding the admin role, ADR 0027). **Strict**
  mode only runs verified definitions.
- **Four-eyes** — nobody approves their own proposal or verifies a version they authored,
  unless they are a human admin (so single-user local mode works). Agents never approve or
  verify their own work, whatever their role, and verify at all only with the admin role
  (ADR 0007, ADR 0018, ADR 0027).
- **Roles, tokens and tools** (ADR 0027) — the permission unit is the tool (`search`,
  `compile_and_run`, `propose_definition`, `review_proposal`, `verify`, `manage_user`, …).
  Roles are configurable (Settings → Roles): **Allowed** tools are the ceiling, **On by
  default** what a new token gets. Each person has many named personal access tokens
  (`cfg_pat_…`, shown once, stored as hashes) with optional expiry and their own tool list
  (follow the role, or only these). MCP lists only the tools a token may use; the API and
  CLI enforce the same tools. Agents never see tokens; admin agents may manage non-admin
  users and roles.
- **Join map** — joins are mined from every definition's SQL (also through CTEs) and used to
  label new SQL. People and agents add what mining can't see (relationship — checked
  against the data —, traps, notes) through proposals; an edge an agent adds counts as known
  only after a person approves it.
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
| `search-index rebuild [--force]` · `search-index download-model` | Meaning-search vectors and model (see Search) |
| `db init` | Create the database |
| `user add NAME [--role R] [--agent] [--bootstrap]` · `user list\|role\|deactivate\|activate` | Users (first token printed once) |
| `token create NAME [--user U] [--expires D] [--only T,…]` · `token list\|revoke` | Personal access tokens (printed once) |
| `role list\|create\|edit\|delete` | Roles: allowed / on-by-default tools |
| `settings show` · `settings mode strict\|open` | Team mode |
| `proposals list [--status pending]` · `show` · `approve [--verify] [--comment]` · `reject --comment` | Review |
| `verify ID VERSION` · `revalidate [IDS]` | Verify a version; drift check against the warehouse |
| `export DIR` · `import-library DIR` | The library as YAML files (git optional) |
| `parse` · `import` · `render` | File-based tools from M1 (no server) |

Environment: `CTEFORGE_DB_URL` (default `sqlite:///cteforge.db`; Postgres via SQLAlchemy URL),
`CTEFORGE_CONNECTIONS` (path to connections.yml), `CTEFORGE_DEFAULT_PROFILE`,
`CTEFORGE_AUTH` (`on`; `off` makes every HTTP call the local admin and is refused unless bound
to localhost), `CTEFORGE_USER` (stdio MCP and CLI), `CTEFORGE_TOKEN` (stdio MCP acts as this
token's user and tools), `CTEFORGE_SEARCH_EMBEDDINGS` (`on`; `off` =
keyword search only), `CTEFORGE_EMBED_CACHE` (embedding model directory).

## Warehouse profiles

Profiles live in a `connections.yml` (see
[examples/connections.example.yml](examples/connections.example.yml)). Secrets are **never**
written there: `secrets_env` names the environment variable that holds each one, resolved
only at connect time and never logged or returned (ADR 0020). Drivers other than DuckDB are
extras: `pip install 'cteforge[postgres]'`, `[redshift]` (AWS `redshift_connector`; Redshift is
its own dialect, ADR 0022), `[snowflake]`, `[bigquery]`, `[databricks]`, `[clickhouse]`
(ClickHouse Inc.'s `clickhouse-connect`, HTTP/HTTPS; ADR 0025), `[mysql]` (PyMySQL; MySQL 8,
MariaDB untested; ADR 0028).

```yaml
profiles:
  analytics_pg:
    dialect: postgres
    options: {host: db.internal, port: 5432, dbname: analytics, user: cteforge_ro}
    secrets_env: {password: PG_PASSWORD}
    statement_timeout_s: 120   # default 60; null turns it off
  warehouse_rs:
    dialect: redshift          # host, port, database, user, ssl, sslmode, timeout, ...
    options: {host: wh.abc123.us-east-1.redshift.amazonaws.com, port: 5439, database: analytics, user: cteforge_ro}
    secrets_env: {password: RS_PASSWORD}   # password auth only; IAM/IdP options are refused
  traces_ch:
    dialect: clickhouse        # host, port, database, user, secure, verify, ca_cert, ...
    options: {host: ch.internal, port: 8443, secure: true, database: agent_traces, user: cteforge_ro}
    secrets_env: {password: CH_PASSWORD}
    # optional scan guards, sent with every query (unset by default):
    # options: {..., max_rows_to_read: 500000000, max_bytes_to_read: 50000000000}
  shop_mysql:
    dialect: mysql             # host, port, database, user, ssl_ca, ssl_verify_cert, ...
    options: {host: mysql.internal, port: 3306, database: shop, user: cteforge_ro}
    secrets_env: {password: MYSQL_PASSWORD}
    # TLS is verified against the system trust store by default; for a private CA:
    # options: {..., ssl_ca: /etc/ssl/mysql-ca.pem}
```

Every statement is cut off after the profile's `statement_timeout_s` and fails with
`query timed out after Ns` (DuckDB: interrupt; Postgres and Redshift: transaction-local
`statement_timeout`; Snowflake: `STATEMENT_TIMEOUT_IN_SECONDS`; BigQuery: job timeout and
cancel; Databricks: `STATEMENT_TIMEOUT` session configuration; ClickHouse: `max_execution_time`
on every request; MySQL: session `max_execution_time` in every call's transaction).

**Always connect as a dedicated read-only role.** cteforge refuses non-SELECT SQL, enforces
row limits and uses read-only sessions where the warehouse has them, but grants are the real
guarantee:

| Warehouse | Read-only setup |
|---|---|
| DuckDB | Opened read-only with external access disabled; one SELECT per call. No setup. |
| Postgres | Every transaction is `BEGIN READ ONLY` and rolled back. Still use a role with only `USAGE` + `SELECT` (`CREATE ROLE cteforge_ro LOGIN; GRANT USAGE ON SCHEMA s TO cteforge_ro; GRANT SELECT ON ALL TABLES IN SCHEMA s TO cteforge_ro;`). |
| Redshift | Every call is its own `BEGIN READ ONLY` transaction, rolled back. Still use a user with only `USAGE` + `SELECT` (`CREATE USER cteforge_ro PASSWORD '…'; GRANT USAGE ON SCHEMA s TO cteforge_ro; GRANT SELECT ON ALL TABLES IN SCHEMA s TO cteforge_ro;`; Spectrum: `GRANT USAGE ON SCHEMA <external schema>`), no superuser, not the cluster owner. Column types come from `SVV_COLUMNS`, which every user can read for all local tables unless the admin enables metadata security (`ALTER SYSTEM SET metadata_security = true`). |
| ClickHouse | Every request carries `readonly = 2` (reads only; `readonly` itself can't be changed), `max_execution_time` and the optional scan guards; one statement per HTTP request. `readonly = 2` still lets a query's own `SETTINGS` clause raise other limits, so give the user a settings profile with constraints and only SELECT grants: `CREATE SETTINGS PROFILE cteforge_ro_profile SETTINGS readonly = 2 CONST, allow_ddl = 0 CONST, max_execution_time = 60 MAX 300, max_memory_usage = 10000000000 MAX 10000000000; CREATE USER cteforge_ro IDENTIFIED WITH sha256_password BY '…' SETTINGS PROFILE cteforge_ro_profile; GRANT SELECT ON agent_traces.* TO cteforge_ro;` No grants on table-function sources (`url`, `s3`, `remote`, …): those read outside the warehouse and are governed by grants, not readonly. Not `readonly = 1`: the connector's own per-request timeout would then be refused. |
| MySQL | Every call is its own `START TRANSACTION READ ONLY`, rolled back; one statement per call (multi-statements are never enabled); TLS verified by default. READ ONLY does **not** stop `SELECT … INTO OUTFILE` or `LOAD_FILE()` (cteforge refuses them before anything runs), so the grants are the guarantee: `CREATE USER 'cteforge_ro'@'%' IDENTIFIED BY '…'; GRANT SELECT ON shop.* TO 'cteforge_ro'@'%';` and nothing else — no `FILE`, `PROCESS`, `SUPER`, `CREATE TEMPORARY TABLES` or write privileges. The user's `sql_mode` must not include `NO_BACKSLASH_ESCAPES`, `ANSI_QUOTES` or `PIPES_AS_CONCAT` (the connector refuses to start). MariaDB is untested. |
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
  (sqlglot only in parsing/templating/compose)            audit/ · files/ · embedding/
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
[0020 warehouse connectors](docs/decisions/0020-warehouse-connectors.md) ·
[0023 meaning search](docs/decisions/0023-meaning-search.md).

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
- Only **DuckDB** is tested against a real warehouse. The Postgres, Redshift, Snowflake,
  BigQuery, Databricks, ClickHouse and MySQL connectors (including their statement timeouts) are
  unit-tested against fake drivers and still need validation on real warehouses
  (ClickHouse and MySQL have opt-in live suites: `tests/integration/test_clickhouse_live.py`,
  `tests/integration/test_mysql_live.py`). The library store is
  tested on SQLite; Postgres is untested in CI.
- A query is not cancelled when the client disconnects; it runs until it finishes or hits
  its profile's `statement_timeout_s`.
- Cross-dialect rendering uses sqlglot (pinned at 30.31.0) and is labeled and lowers trust.
  Known transpile defects (kept visible in `tests/golden/`): Postgres has no
  `ROUND(double precision, int)`; BigQuery gets `TIMESTAMP_TRUNC` / `PARSE_TIMESTAMP` on
  `DATETIME` columns; Postgres and Redshift `TO_TIMESTAMP` are zone-aware against naive columns. Week
  starts differ by warehouse (DESIGN §13). Prefer rendering in the source dialect.
- `cteforge demo` needs a source checkout (it reads `examples/`).

## License

Apache-2.0
