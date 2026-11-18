# cteforge — Design

> Source of truth for *what* cteforge is and *why* it is built this way.
> Individual decisions are recorded in [`decisions/`](decisions/). When code changes a
> decision, update this file and the relevant ADR in the same commit.

Status: pre-alpha. M1–M5 built and wired end to end (`cteforge demo` → `cteforge serve`: GUI, HTTP API, MCP over
stdio and streamable HTTP); M6 connectors built; DuckDB, Postgres, ClickHouse and MySQL are verified against
real engines in CI; the cloud warehouses (Redshift, Snowflake, BigQuery, Databricks) are not (see §16).

---

## 1. Problem & idea

AI agents that write SQL get business questions wrong in ways that look right. They don't
know what a metric actually means, which joins are safe, or which logic must stay fixed.
Teams already have queries they trust, but agents can't find or reuse them in a structured way.

**cteforge** is an open-source server (GUI + MCP) that turns a team's trusted SQL into a
library agents can search, reuse, change safely, and build on.

Origin: using an internal SQL-definitions tool in a way it wasn't designed for — storing
verified SQL with plain-language descriptions and instructions for combining CTEs — showed
that this was where the real value was. cteforge builds that use directly into the tools.

## 2. Positioning

| | Semantic layers (WrenAI MDL, dbt Semantic Layer, Cube) | cteforge |
|---|---|---|
| Starting point | Model tables, joins, metrics first | Paste SQL you already trust |
| What you describe | The whole data world | Only the parts of a query that change ("the holes") |
| Useful when | The model is mostly complete | After the first query is imported |
| Complex CTE logic | Hard to express as measures/dimensions | Kept as written (formatting normalized by sqlglot; meaning proven by the same-query-back check) |

One line: **"Paste the SQL you already trust. No semantic model required."**

A semantic model still emerges bottom-up: fixed CTE logic ≈ metrics, the join map ≈
relationships, changeable variables ≈ dimensions/filters, composed definitions ≈ saved
queries.
Export to WrenAI/dbt formats is a possible later feature, not a goal. See ADR 0001, 0002.

## 3. Core concepts

- **Definition** — one trusted query plus: title, plain-language meaning, keywords/synonyms,
  "use when", "don't use when", source dialect, tables read, output columns, **grain**
  (e.g. one row per account per month), join keys, variables, state, verifier + date, version.
  Its body is either **SQL** (imported trusted SQL) or a **composition** (ADR 0026, §17):
  other definitions used as parts by alias (`uses`: alias → `{id, version?}`), a final
  SELECT over the aliases (the definition's template; its own literals can be annotated)
  and `fixed` values it always sets on its parts. Parts are live references: a part
  without `version` floats to its latest usable version, so fixing a definition fixes
  everything composed from it; a `version` pins it. A composed definition's variables are
  its parts' open variables, named `<alias>.<var>`. Any definition can be a part (nesting
  is allowed; cycles are refused).
- **Variable** — a typed hole in a definition's SQL (§4), with a role: `changeable` or `fixed`.
- **Join map** — join edges mined from every applied definition's SQL (composed ones from
  their compiled SQL): tables, keys, join type, the definitions it was mined from
  (`sources`), and what mining cannot see — `relationship`, `traps`, `notes` — added
  through proposals (`propose_join` annotate / add). An edge is **known** (counts for trust
  labels) when it is mined from a definition a person stands behind (authored or approved
  by a human, or verified), added by a person or confirmed by a person; an edge an agent
  adds never auto-applies, and neither it nor one mined only from agents' auto-applied
  definitions is known until a person approves, verifies or confirms it.
  **Confirmed**: a person approved it, or it was mined from a verified definition. A
  reference for exploration, not a restriction.
- **Trust label** — attached to every result (§8).
- **Proposal** — any change (new/edited definition, join annotation or new edge, rename)
  from a person or an agent, pending approval according to permissions (§18).
- **Issue** — a structured problem or warning (`code`, `severity`, `message`, optional
  `variable`/`literal`) returned by every check, render and run, so agents and the GUI can
  act on it instead of parsing prose.

## 4. Typed variables

Each type has **one strict input format** and **one render rule per dialect**. The LLM supplies
values in the input format; the tool produces SQL. See ADR 0005, 0006.

| Type | Input format | Notes |
|---|---|---|
| `date_range` | `{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}` | Covers two literals (a lower and an upper bound). Shape recorded only for suggested pairs (ADR 0015): `half_open` [a, b), `inclusive` [a, b], `exclusive` (a, b), `start_exclusive` (a, b]; otherwise `null` and a description is required. Optional `format`. |
| `date` | `"YYYY-MM-DD"` | Optional `format` (strftime) for bare non-ISO date strings (ADR 0014). |
| `grain` (M1.5) | `"day" \| "week" \| "month" \| "quarter" \| "year"` (lower case) | Names date truncations by grain ID (`<scope>.G<n>`). Never a placeholder (argument order and unit syntax differ; Databricks' parser can't keep a placeholder as a unit): the template keeps the truncation as written, and render rebuilds the whole node in the source dialect for another unit. Every place must truncate to the same original unit, one of the five; a unit read from a column (`DATE_TRUNC(cfg.month, ts)`) is never a grain. A grain that leaves other truncations to its unit as written (GROUP BY copy, a CTE joined on the period, a UNION branch) is a `grain_partial` error. Week start: §13. |
| `text_list` | `["a", "b"]` | Replaces a whole IN list. Empty list is rejected (would render `IN ()`). |
| `text` | `"a"` | Exact text. Wildcards/patterns belong in the SQL (`'%' \|\| $x \|\| '%'`). |
| `number` | `123` / `1.5` | Finite only (no inf/NaN). Exact decimals kept (`1000.50`). Optional `unit` label (e.g. `TFLOPS`). |
| `boolean` | `true` / `false` | |

Rules:
- **The analyst decides** (ADR 0015). Every filter literal gets an ID; each is left alone,
  made a `fixed` variable (named, described, visible to agents; overriding warns) or a
  `changeable` variable. Nothing is changeable by default. The tool reports structural facts
  per literal (`negated`, `or_branch`, `case_condition`, `join_on`, `outer_join_on`,
  `having`, `subquery`, value-side wrapper) and *suggests* range pairs; it never assumes
  meaning.
- **The SQL determines the format** (ADR 0014). Agents send plain values (ISO dates, plain
  numbers, plain text). Casts, functions, arithmetic and concatenation around a value stay
  exactly as the analyst wrote them; only the literal is replaced.
- One variable may bind the same filter in several places (e.g. the same period in two
  CTEs); every place must hold the same original value.
- `column` (what the variable filters) is annotated by a person or LLM. `column_type` is
  **always read from the warehouse**, never supplied by an LLM. Identifiers are matched
  case-insensitively (unquoted) with schema qualifiers kept; when the column side is an
  expression (`CAST(col AS DATE)`, `EXTRACT(YEAR FROM col)`), the expression's type is used.
- Optional `allowed` values: values outside the list are **warned on, not blocked**, and lower
  the trust label.
- Inputs like `"2026-Q3"` are rejected with a precise error; the agent translates meaning
  ("Q3 2026") into the format. Business-specific meaning (e.g. fiscal quarters) lives in the
  definition's notes.

## 5. Division of labor

- **LLM:** understands the question, chooses definitions, chooses variable values, writes only
  genuinely new SQL (a small final `SELECT`, or exploratory queries).
- **Tool:** validates values, renders SQL deterministically, checks it, runs it, labels it.
- Trusted SQL **never round-trips through the LLM**. Agents call by reference
  (definition IDs + values), so a 200-line verified CTE cannot be "helpfully" altered.

## 6. MCP tools (M4)

| Use the library | Build the library |
|---|---|
| `search(query)` — ranked definitions + their variables | `parse_query(sql, dialect?)` — literals with IDs, columns + types, structural facts, range/grain suggestions, joins |
| `get_definition(id)` — full card: meaning, variables with input formats, outputs, grain, used by, joins to; for a composed definition its parts, fixed/open variables and compiled SQL | `propose_definition(sql \| composition, dialect?, metadata, annotations?)` — exactly one of `sql` / `composition` ({uses, select?, fixed?}; parts need no annotations; an omitted `dialect`: §12) |
| `compile_and_run(uses, values, select?)` — composed definitions are used by id like any other (values `<alias>.<var>`); `run=false` returns the full compiled SQL | `revise_definition(id, base_version, sql? \| composition?, metadata?, annotations?)`; with `new_id` alone it proposes a rename (ADR 0024) |
| `check_and_run(sql)` — exploratory, labeled; SQL that is a definition's original query gets a `matches_definition` hint to use `compile_and_run` instead | `propose_join(action: annotate \| add, …)` — relationship / traps / notes on an existing edge, or an edge no definition contains (checked against the warehouse; an agent's `add` never auto-applies) |
| `list_joins(table?, known_only?)` · `describe_tables(tables?)` — discovery: join-map edges (keys, relationship, traps, notes, sources, known, confirmed), columns + types (+ units the library declares) | (`version`, `state`, `schema_version` are set by cteforge, never by the caller) |

| Review (when allowed) | Admin (when allowed) |
|---|---|
| `list_proposals(status?)` — proposals with checks, diff and impact | `list_users()` — users, roles, token names / prefixes / last use (never token values) |
| `review_proposal(action: approve \| reject \| verify, id? \| definition_id + version, comment)` | `manage_user(action: create \| set_role \| set_tools \| deactivate \| activate \| revoke_token, …)` · `manage_role(action: create \| edit \| delete, …)` |

**Tool access (ADR 0027).** The permission unit is the tool: `tools/list` shows only the
tools the caller's token allows (its user's role ∩ the token's own list; stdio without a
token: the user's role defaults), and calling any other tool is refused
(PermissionDeniedError) and audited. The same tools are checked in services, so the HTTP API
is not a way around them. Write tools carry `readOnlyHint: false` (and `destructiveHint`
for review, user and role management) so clients can still ask the person first. No tool
ever returns a token. Approving and verifying are open to agents only through
`review_proposal`, within the four-eyes rule (no exemption for agents) and, for verify and
changes to verified work, only with the admin role. The GUI (M5) drives the same services;
MCP and HTTP are thin adapters.

## 7. Proposal checks

Run automatically on every proposed/revised definition. SQL that is not one read-only
SELECT/UNION (the same rule as composition, §17) is refused first (`not_read_only`), before
anything reaches the warehouse.
1. **Same query back** — serialize template → re-parse → render with the original values →
   AST-equal to the parsed original. Then run both on the warehouse and compare results
   (the result comparison is the real proof; the AST check catches most errors cheaply).
2. **Consistency** — every placeholder has a declared variable; every variable's type is
   compatible with its column's real type.
3. **Compiles** on the target warehouse (EXPLAIN / dry run).

## 8. Trust

States: `draft` → `llm_annotated` → `verified`.

| State | Meaning | Usable by agents |
|---|---|---|
| `draft` | Proposed; checks failed or not run | No |
| `llm_annotated` | Passed checks; no human review | Yes, labeled |
| `verified` | Approved by a person (or an admin agent, ADR 0027) | Yes, labeled with verifier (+ kind) + date |

Result labels, best to worst: `verified` · `built_from_verified_parts` · `llm_annotated` ·
`new_logic_on_known_joins` · `exploratory`. Policy (trust/, pure):
- One verified definition, no final SELECT → `verified`. A final SELECT over verified
  definitions joining only on known keys → `built_from_verified_parts`.
- A composed definition (ADR 0026) no person has verified is labeled like an agent's final
  SELECT over its parts: `built_from_verified_parts` at best, `exploratory` when its final
  SELECT joins on unknown keys. Once a person verifies it, its final SELECT and choice of
  parts count as reviewed (`verified` over verified parts); its parts' states still cap
  the label (verifying the whole does not verify the parts). A floating part with no
  usable version makes it unusable (`part_not_usable`); a pinned part that was superseded
  is shown (`part_superseded`).
- Known joins (§3): mined from a definition a person stands behind (authored by a human,
  approved by a human without auto-apply, or verified), added by a person, or confirmed by
  a person (changing a confirmed edge's annotations keeps it confirmed only if a person
  approves).
  An agent cannot raise labels by declaring joins.
- Any `llm_annotated` definition caps the label at `llm_annotated`.
- Raw SQL (`check_and_run`) → `new_logic_on_known_joins` if every join matches the join map,
  else `exploratory`. Joins in a final SELECT that aren't known → `exploratory`.
- Each downgrade (fixed-variable override, out-of-`allowed` value, cross-dialect transpile)
  lowers the label one step.
- Drafts are never usable.

Team setting: **strict** (only verified usable) or **open** (llm_annotated usable, labeled).
An LLM can create and improve unverified definitions; **changes to a verified definition
always require review by a person**, or by an agent holding the admin role (ADR 0027;
four-eyes still applies). See ADR 0007.

## 9. Server architecture (M3+)

Sign-in roadmap (ADR 0027): tokens now → optional OIDC GUI sign-in → optional MCP OAuth
with cteforge as its own authorization server.

- Self-hosted server: Postgres (SQLite for local single-user), web GUI, MCP endpoint.
- Every change is a proposal. **Roles are configurable** (ADR 0027): each role has
  **Allowed** tools (the ceiling) and **On by default** tools (what a new token starts
  with) from one catalog — use (`search`, `get_definition`, `compile_and_run`,
  `check_and_run`, `describe_tables`, `list_joins`), build (`parse_query`,
  `propose_definition`, `revise_definition`, `propose_join`), review
  (`list_proposals`, `review_proposal`, `verify`), admin (`view_audit`, `list_users`,
  `manage_user`, `manage_role`, `manage_settings`) — plus `review_all` (approve anything,
  else only what the user owns). Built-ins keep today's behaviour: viewer (use +
  list_proposals), contributor (+ build, review_proposal for what they own), approver
  (+ review everything, verify, view_audit), admin (everything; its Allowed list is fixed).
  Admin tools other than `view_audit` exist only in the admin role. A role in use cannot
  be deleted; built-ins can be edited, not deleted.
- **Personal access tokens** (GitHub-style): many named tokens per user (`cfg_pat_` + 43
  random characters), shown once, stored as SHA-256 hashes, with a 12-character prefix,
  created/last-used times (last use written at most once a minute), an optional expiry and
  their own tool list: **follow role** (role defaults + include − exclude; new defaults
  follow) or **only these**. Effective tools = role Allowed ∩ the token's list, minus
  `verify` for an agent whose role is not admin. A revoked or expired token, or a
  deactivated user, gets the same generic 401 on the next call. People generate their own
  tokens (Settings → My tokens, `cteforge token create`); a human admin generates tokens for
  any user (so `manage_user` is effectively full admin power); agents never receive a token
  from any surface. A token never creates a token of its own user with tools it does not
  hold itself, that outlives it, or that follows the role when it is itself `only`; such a
  token records its parent, and revoking a token revokes everything minted from it.
- **Admin agents** (agent kind + admin role) may create users (no token returned), change
  role / tools / active of non-admin users, revoke non-admin users' tokens, create and
  edit roles other than admin, approve / reject and verify (four-eyes, no exemption).
  Human-only: seeing or minting tokens, anything touching an admin user, granting the admin
  role, editing the admin role. The last active admin cannot be demoted or deactivated.
- **Users** have a name (login), a kind (human | agent), a role, an active flag and, since
  ADR 0029, an optional **display name** and **email** (≤ 80 / ≤ 254 characters; the
  email's format is checked lightly). A user sets their own (`PUT /me/profile`); an admin
  with `manage_user` sets anyone's (`PATCH /users/{name}`, `cteforge user profile`; agent
  admins never an admin user's). They are shown in the GUI's user menu, Users & roles,
  token lists and as the Log's actor (the email only to callers holding `list_users`),
  never in search results or on definition cards. Changes are audited.
- Full version history per definition with diffs; **impact preview** ("used by 14 composed
  definitions").
- **GUI** (ADR 0019, 0026, 0029): a sidebar — **Library · Join map · Import · Log**, then
  **Settings** (Connections, Team mode, Users & roles, Roles, My tokens; admin sections
  only with their tools) — and a header with the default connection and the user menu
  (name, email, role, personal access tokens, the token's tools, theme, sign out). Warm
  neutral background, dark green primary, trust icons (verified ✓, LLM-annotated ◐,
  draft ◌), monospace for ids and SQL, light and dark. The **Library** searches and filters
  (Trust, Approval, Created by, Verified by), sorts by relevance then trust, and shows
  "Waiting on you": the proposals the person may decide, oldest first, with +/− changed
  lines. A **definition** page shows its usage notes, the variables agents can fill with
  their original values, the trusted SQL (or a composed definition's parts and compiled
  SQL), and its grain, outputs, joins, users, keywords and owner; changes go through
  "Propose a change". A **proposal** page shows the proposer's note, the **query** diff
  (the trusted SQL as written, or a composed definition's parts and final SELECT; unified
  or side by side, changes only or full) and the described fields that change (before →
  after), used by, the decision and its real activity. The
  **Join map** is a list: many side / one side, keys, join-type variants per source
  definition and where each joins (CTE, main query, subquery), the sources' trust, known
  / confirmed, and per row the sources, evidence, traps and notes, annotated through
  proposals. The **Log** is the audit log (§11). **The GUI does not run queries**: agents
  run through MCP and scheduled jobs through `POST /run` / `POST /check` or the CLI.
- Git is an optional export/import, not the source of truth. See ADR 0003.
- Users and settings: team settings (strict/open mode, **default connection**;
  `manage_settings`) and users/roles/tokens (`list_users`, `manage_user`, `manage_role`)
  belong to the admin role. The default connection decides the default dialect:
  the dialect follows the connection (ADR 0021). Resolution: the admin's setting →
  `CTEFORGE_DEFAULT_PROFILE` → the only profile → none.

## 10. Warehouses

Targets (free, permissive drivers): Postgres, Redshift (its own dialect, ADR 0022), Snowflake,
BigQuery, Databricks, ClickHouse (ADR 0025), MySQL 8 (ADR 0028; MariaDB untested), DuckDB. A team may connect several; each definition maps to one
connection, and definitions on different connections are never combined in one query.
DuckDB first (M1); the rest in M6.

Redshift uses AWS's `redshift_connector` (password auth only for now) and reads column types
from `SVV_COLUMNS`, so late-binding views and Spectrum external tables are covered.

ClickHouse uses ClickHouse Inc.'s `clickhouse-connect` over HTTP(S) (password auth, a named
read-only user). Every request carries `readonly = 2` (reads only; the query cannot lift
it), `max_execution_time` and optional scan guards (`max_rows_to_read`, `max_bytes_to_read`);
the server runs one statement per request. Column types come from `system.columns`; names
are `database.table`. Common ClickHouse filter shapes on trace-like data are variables:
`has(arr, 'x')`, `hasAny` / `hasAll` element lists, lambdas (`arrayExists(x -> x = 'a',
arr)`), `ARRAY JOIN` names, `map['k']`, `JSONExtractString(...)`, `match(s, 're')`.
Trusted SQL must not contain ClickHouse query parameters (`{x:String}`): cteforge runs
SQL as written and uses that syntax for its own template placeholders (ADR 0025).

MySQL uses PyMySQL (password auth, a named read-only user, verified TLS by default, charset
utf8mb4). Every call is its own `START TRANSACTION READ ONLY` with a session
`max_execution_time`, then rolled back; the server refuses multi-statement strings (the
driver never enables them). Column types come from `information_schema.columns`
(`column_type`: `tinyint(1)`, `int unsigned`, `enum(...)`); names are `database.table`.
cteforge reads MySQL as under the default sql_mode; the connector refuses a session with
NO_BACKSLASH_ESCAPES, ANSI_QUOTES or PIPES_AS_CONCAT. Trusted MySQL SQL must not contain
executable comments (`/*! ... */`) or `MAX_EXECUTION_TIME` / `SET_VAR` hints: MySQL runs the
former, which no check can see, and the latter would lift the statement timeout (ADR 0028).

Used for: column types, compile checks (EXPLAIN / BigQuery dry run), running queries,
and **nightly re-validation** of every definition to detect schema drift. Credentials stay
on the self-hosted server. See ADR 0004.

## 11. Audit (M3)

cteforge logs **its own** tool calls: question, definitions + versions, variable values, trust
label, result metadata (row count, duration, accepted/rejected). It does **not** duplicate the
warehouse's query history and does **not** store result rows by default.

What is logged (ADR 0008 addendum, ADR 0029): every MCP tool call (reads included, so
people see what agents looked up), every run (`compile_and_run`, `check_and_run`) and every
change from any surface, and every refused or failed call. A person's successful read
through the GUI / HTTP API or the CLI is not logged.

Each event records the caller's token by id and 12-character prefix (never the token or
its hash) and, for runs, the connection used. The GUI shows the audit log as the **Log**
(ADR 0029): real call names (kind + operation), actor, definitions, trust, outcome, rows,
duration, compile ID; a row opens the values, question, token prefix and connection.

Every compiled query carries a tag that lands in the warehouse's own logs:
`/* cteforge: realization_rate v3 compile_id=8f2a */` — so the two logs can be joined.
See ADR 0008.

## 12. Dialect policy

A definition records its **source dialect** and renders natively in it. Rendering for a
different dialect (via sqlglot transpile) is allowed but labeled and lowers trust, because
transpilation can subtly change semantics. See ADR 0013. Where it happens:
- Running a definition on a profile of another dialect transpiles it (`transpiled` issue).
- `compile_and_run(dialect=X, run=false)` compiles for X even when the profile's dialect
  differs (no EXPLAIN: `checks_incomplete`); running is always in the profile's dialect.
- Importing SQL in another dialect than the checking profile's runs the §7 checks on the
  SQL transpiled to the profile's dialect, with a `dialect_mismatch` warning; the
  definition keeps its source dialect.

Which dialect pasted SQL is in: callers of `parse_query` / `propose_definition` (MCP, API,
GUI) may omit `dialect`. It then resolves, in order, to: the **explicit** argument → the
**connection's** dialect (the profile passed, else the default connection, §9) → otherwise
a PolicyError saying how to fix it (ADR 0021). The GUI's Import page starts with the
default connection and its dialect; picking another connection switches the dialect, and
the dropdown stays so one import can use another dialect (`dialect_mismatch` above).
Revising a definition keeps its source dialect.

## 13. Time & NULL semantics

- Typed literals take the **column's real type**: e.g. BigQuery `TIMESTAMP` (zone-aware) vs
  `DATETIME`; Snowflake `TIMESTAMP_NTZ` vs `TIMESTAMP_TZ`. sqlglot's generic `TIMESTAMP` maps
  to BigQuery `DATETIME`, so the warehouse type must drive the cast.
- Week start differs by warehouse (affects `grain=week`). `week` always renders the
  dialect's native week truncation:
  - DuckDB `DATE_TRUNC('WEEK', x)`: Monday (ISO). Verified on DuckDB.
  - Postgres `DATE_TRUNC('WEEK', x)`: Monday (ISO), per the Postgres docs.
  - Redshift `DATE_TRUNC('week', x)`: Monday, per the Redshift docs (returns TIMESTAMP).
  - Snowflake `DATE_TRUNC('WEEK', x)`: depends on the session/account `WEEK_START`
    parameter. The default 0 (legacy, ISO-like) starts weeks on Monday, per the Snowflake
    docs; an account that sets `WEEK_START` gets its own start day.
  - BigQuery `DATE_TRUNC(x, WEEK)` (and TIMESTAMP_/DATETIME_TRUNC): Sunday. sqlglot parses
    `WEEK` as `WEEK(SUNDAY)`. `WEEK(MONDAY)` and `ISOWEEK` exist but are not grain values;
    a truncation written that way can't be a grain variable.
  - Databricks `DATE_TRUNC('WEEK', x)` / `TRUNC(d, 'WEEK')`: Monday, per the Databricks/Spark
    docs. `TRUNC(date, fmt)` has no day format, so grain `day` is refused there.
  - ClickHouse: `toMonday(x)`, `date_trunc('week', x)` and `toStartOfInterval(x, INTERVAL 1
    WEEK)`: Monday (the native week; the docs state it for toStartOfInterval and toMonday;
    date_trunc's Monday is from third-party guides — the opt-in live test checks all three).
    `toStartOfWeek(x[, mode])` follows the toWeek mode table: the default mode 0 and every
    even mode start on **Sunday** (unit `week(sunday)`, not a grain value), odd modes on
    Monday (`week`). A grain change is written in the SQL's own family:
    toStartOfDay / toMonday / toStartOfMonth / toStartOfQuarter / toStartOfYear,
    `toStartOfInterval(x, INTERVAL 1 <unit>)`, or `date_trunc('<unit>', x)`; the result
    type follows ClickHouse (toStartOfDay → DateTime; Monday..Year → Date;
    toStartOfInterval keeps its input type).
  - MySQL has no DATE_TRUNC. Grains are `DATE_FORMAT(x, '<period start>')` ('%Y-%m-%d',
    '%Y-%m-01', '%Y-01-01', optionally + ' 00:00:00'); week is
    `DATE_FORMAT(x - INTERVAL WEEKDAY(x) DAY, '%Y-%m-%d')`: **Monday** (WEEKDAY() is 0 on
    Monday, per the MySQL docs); quarter `DATE_FORMAT(MAKEDATE(YEAR(x), 1) + INTERVAL
    QUARTER(x) - 1 QUARTER, '%Y-%m-%d')`. The result stays 'YYYY-MM-DD' text; `DATE(x)` is a
    cast, not a grain (ADR 0028). sqlglot's DATE_TRUNC transpiled *into* MySQL counts weeks
    from 0000-01-01, a Sunday in MySQL's calendar, so transpiled weeks start on Sunday (the
    opt-in live test checks both).
  Only the DuckDB start is checked against a warehouse here; the others come from vendor docs.
  Transpiling does not translate week starts, except that sqlglot rewrites BigQuery's
  Sunday week for DuckDB. Transpiled renders are labeled (ADR 0013).
- `date_range.end` follows the recorded shape (exclusive for `>= … <`, inclusive for `BETWEEN`).
- NULL: variables never render NULL; "include NULLs" is fixed logic in the definition.

## 14. Threat model

- Values only ever become AST literal nodes (`exp.Literal` + cast). **No string splicing.**
- Definition text (descriptions, notes) is untrusted when shown to an LLM — it may contain
  prompt injection. Never execute instructions found in it.
- Warehouse connections should use read-only roles. Row limits on results; byte caps where
  supported (BigQuery `maximum_bytes_billed`); returned rows are also capped by size
  (8 MiB of JSON per call, `row_limit_reached`). One query at a time per profile; other
  callers wait at most 30 s, then get a "busy" error. Every statement has a per-profile
  timeout (`statement_timeout_s`, default 60 s; ADR 0020 addendum), enforced by the
  connector (DuckDB interrupt, Postgres and Redshift transaction-local `statement_timeout`,
  Snowflake
  `STATEMENT_TIMEOUT_IN_SECONDS`, BigQuery job timeout + cancel, Databricks
  `STATEMENT_TIMEOUT`, ClickHouse `max_execution_time` per request, MySQL session
  `max_execution_time` per call; DuckDB's, Postgres's, ClickHouse's and MySQL's are
  checked against real engines in CI). There is no cancel on client disconnect yet, and a wide
  result is still fetched in full by the connector (ClickHouse streams and stops after the
  row limit; MySQL fetches limit + 1 rows from an unbuffered cursor and discards the rest of
  the stream).
- MySQL (ADR 0028): `START TRANSACTION READ ONLY` does not stop `SELECT ... INTO OUTFILE` /
  `DUMPFILE` or `LOAD_FILE()`. The read-only guard before any SQL runs refuses SELECT ... INTO
  (INTO OUTFILE does not even parse), executable comments `/*! ... */` (MySQL runs their
  content; checks would see a comment), `MAX_EXECUTION_TIME` / `SET_VAR` hints and MySQL
  side-effect functions (LOAD_FILE, SLEEP, BENCHMARK, GET_LOCK, ...). The guarantee is the
  grants: SELECT only, no FILE / PROCESS / SUPER / CREATE TEMPORARY TABLES. Rendered text
  values are escaped for backslash-escape MySQL over a utf8mb4 connection; servers whose
  sql_mode changes how literals or `"` / `||` read are refused.
- ClickHouse (ADR 0025): a query may raise its own limits in a `SETTINGS` clause
  (`readonly = 2` allows changing settings other than `readonly`). Only the read-only
  user's settings profile can cap them (constraints such as `max_execution_time = 60 MAX
  300`, `readonly = 2 CONST`); the README gives the recommended profile. Table functions
  that read outside the warehouse (`url`, `s3`, `remote`, ...) are governed by grants, not
  by readonly: grant the user SELECT on its databases only.
- Proposal check runs never return the warehouse's runtime error text (it can quote data
  values, and proposals are readable by every viewer, also in strict mode); only its class.
- `CTEFORGE_AUTH=off` accepts only connections whose socket is loopback (peer and local
  address), whatever the Host header says (ADR 0018).
- Tokens (ADR 0027): stored only as SHA-256 hashes; the value is returned once, in the
  response that generates it, to a human caller only (never through MCP or to an agent);
  every /api response is `no-store`; the audit log records token ids and prefixes, never
  values or hashes. Unknown, revoked or expired tokens and deactivated users all get the
  same generic 401. Tool access is enforced in services for every surface, and MCP hides
  tools a token cannot use. A token's tools never exceed its user's role, and a token
  cannot create a broader, longer-lived or role-following token for its own user (no
  self-escalation or persistence through a narrow, short-lived token someone handed to an
  agent); revoking a token revokes the tokens it minted. A `compile_and_run` final SELECT
  over raw tables needs `check_and_run` too, so it is no way around that tool. Agents
  never see tokens; admin agents cannot touch admin users or the admin role.
- Credentials: environment/profile only, never logged, never returned over MCP/HTTP.
  Database URLs are printed with the password redacted (`postgresql+psycopg://user:***@host/db`).
  Warehouse error messages are scrubbed of connection details; DuckDB's also of host
  paths (home directory, database location) and the OS user name.

## 15. Identity & versioning

- A definition has an `id` (slug, filled in from the title and locked on import) and an
  integer `version`. The ID can be renamed deliberately: the person sees everything that
  uses it and types the new ID to confirm; it is a proposal like any change; references
  inside cteforge are updated; former IDs stay searchable and a former ID's not-found
  error names the new one; no silent alias (ADR 0024). A rename creates no version: version
  numbers, state and verification are kept, and stored versions are rewritten to the new
  ID (the audit log is not). A former ID is never given to another definition.
- A new version is created by any change to SQL, variables, grain, outputs, or join keys.
  Metadata-only edits (keywords, notes) also bump the version but are flagged as non-logic.
- Parts of a composed definition either **pin** a version or **float** on the latest
  usable version (default; ADR 0026).
- The definition file format carries `schema_version` for format migrations.

## 16. Milestones

| | Scope | Status |
|---|---|---|
| M1 | Parse → typed template → "same query back" → render for Snowflake/BigQuery/Postgres → run on DuckDB; definition file format; CLI | done |
| M1.1 | Fix pass: ADR 0014/0015 (wrappers, structural facts, range pairing, `format`, `unit`, multi-place variables, structured issues), case-insensitive types, finite numbers | done |
| M1.5 | `grain` variable | done |
| M2 | Compose definitions (§17); trust labels (§8) | done |
| M3 | Store (SQLite/Postgres), proposals (§18), versions, permissions (incl. four-eyes), audit | done (Postgres untested in CI; SQLite tested) |
| M4 | MCP server (§6) | done (stdio + streamable HTTP at `/mcp`; e2e-tested) |
| M5 | HTTP API + GUI (Library with review in it, definition pages, import flow, revise, rename, Join map, Log, Settings; ADR 0026 replaced Explore and the Review page; ADR 0029 redesign, no GUI Run) | done; GUI↔API contract e2e-tested |
| M6 | Snowflake/BigQuery/Postgres/Databricks connectors, EXPLAIN/dry-run, statement timeouts, drift check | built; verified against real engines in CI (`.github/workflows/ci.yml`): DuckDB, Postgres 16, ClickHouse 25.8 and MySQL 8.4 (service containers); ClickHouse 26.10 also locally. Redshift, Snowflake, BigQuery and Databricks (incl. their timeouts and read-only setup) are unit-tested against fake drivers only and unverified against real warehouses |

## 17. Composition (M2; composed definitions: ADR 0026)

A composed definition (stored) or an ad-hoc `compile_and_run` names definitions by alias,
gives values per alias, and optionally a final SELECT over those aliases.
- Each definition is rendered on its own (templating; a composed part is itself compiled
  first, recursively), then compose **hoists** its inner CTEs into one top-level WITH,
  renaming them `<alias>__<cte>` to avoid collisions, and adds the definition's final
  SELECT as a CTE named `<alias>`. Order follows dependencies.
- The final SELECT must be a single read-only SELECT that references only the aliases (and,
  with a lower label, raw tables — which also needs the `check_and_run` tool, ADR 0027;
  also for a composed definition's stored final SELECT, and for the proposal checks that
  run it).
  Without one, a single definition is returned as-is.
- Checks: every alias resolves; join conditions between aliases use declared `join_keys`
  (else an `unknown_join` issue); joining definitions of different grain without
  aggregation raises a `fan_out_risk` warning.
- Composed definitions: `propose_definition(composition={uses, select?, fixed?})`. Checks:
  every part resolves, `fixed` names open variables of its part, no cycle (also through
  nesting; refused again when applying and at run time), the select is one read-only
  SELECT, the whole compiles and passes EXPLAIN on the warehouse; with annotations on the
  select, the compiled query with the plain select and with the template give the same
  results. Values: `values[alias]["<part alias>.<var>"]` (or per part); overriding a
  `fixed` value is a `fixed_override`. Reviews of a part list the composed definitions it
  reaches ("used by"); renames rewrite their parts.

## 18. Proposal & review flow (M3)

- Every change is a proposal: `new_definition` (SQL or composed), `revise_definition`,
  `new_join` (`action` add | annotate; ADR 0026), `rename_definition` (ADR 0024: from/to +
  the references seen when proposed; no checks to run; never "approve with verify";
  another rename of the same definition that was overtaken is refused as stale).
  (`new_recipe` remains only on proposals stored before ADR 0026.)
  Checks (§7) run at proposal time and are stored with it; the GUI shows them with a diff,
  an impact preview ("used by": composed definitions using the definition) and, for a
  composed definition, its parts, fixed/open variables and full compiled SQL. A join
  proposal's relationship claim is checked against the warehouse (`COUNT(*)` vs
  `COUNT(DISTINCT key)` per side) and the evidence is stored with it.
- The GUI's Library is the review queue too (ADR 0026): definitions and the proposals
  touching them, filtered by trust, approval, created by and verified by, with a "needs
  my review" filter; proposal detail stays its own page. "Waiting on you" lists what the
  person may decide, oldest first, with the +/− lines of their query ("metadata only" when
  the query does not change; ADR 0029); its link opens the
  needs-my-review filter sorted oldest first (`GET /library?needs_my_review=true&sort=oldest`).
- Auto-apply: in **open** mode, a proposal whose checks all pass and that targets a new or
  unverified definition (or an unconfirmed join edge) is applied immediately as
  `llm_annotated`. An edge an agent adds never auto-applies (ADR 0026). Everything else
  waits. Join proposals are approved, never verified; a person's approval confirms the
  edge.
- Changes to a **verified** definition always wait for review by a person (approver or the
  definition's owner) or an admin agent (ADR 0027). Approving can also verify (the
  `verify` tool: humans whose role allows it, or agents with the admin role). Rejections
  keep a comment. Decisions record the actor's kind (`decided_by_kind`), and a definition
  card shows `verified_by_kind`, so "verified by an agent" is visible.
- **Four-eyes rule** (ADR 0018 addenda, ADR 0027): nobody may approve their own proposal
  or verify a definition version they authored any part of, unless they are a **human**
  admin (so single-user local mode keeps working). An agent admin never approves or
  verifies its own work (no exemption for agents). A version's authors are the author of the newest
  logic-changing version up to it and of every metadata-only version since, so a cosmetic
  revision by someone else does not let the logic's author verify it. Auto-apply is not an
  approval and is unaffected.
- Verify only rests on complete, passing checks: a proposal with an error,
  `checks_incomplete`, `draft_not_usable`, or a definition the checks left `draft` can be
  approved (no errors) but never verified; `verify_definition` refuses a draft version.
- Each decision is a **compare-and-swap** on the stored proposal status (pending →
  approved/rejected/withdrawn), so two server processes on one database cannot both
  decide the same proposal. Approving claims the proposal first, then writes the new
  version; a failed write releases the claim.
- `revise_definition` carries `base_version`; if the definition moved on, it's refused with a
  `stale_base_version` issue.
- A nightly/CLI `revalidate` re-runs §7 checks for every latest version against its
  warehouse profile and reports drift (does not change state automatically).

## 19. Search (M4)

Hybrid (ADR 0023): **keyword** (ADR 0017) and **meaning** results, merged by reciprocal rank
fusion into one `search` call.
- Keyword: BM25-style over title, keywords, meaning, use/don't-use notes, variable names
  and descriptions, outputs, tables and former IDs (ADR 0024). Exact names (tables,
  acronyms, IDs) win here.
- Meaning: each definition version has a generated Markdown **search document** (title,
  id and former ids, meaning, use/don't-use notes, keywords, variables with descriptions, outputs, grain,
  tables, joins). The server embeds it with `bge-small-en-v1.5` (fastembed, CPU, in
  process) and stores the vector in its own database, tagged with the model. A query is
  embedded the same way and compared to every visible definition's vector.
- Merge: reciprocal rank fusion, Σ 1/(60 + rank) over the two rankings. Only definitions
  with similarity ≥ 0.58 and within 0.08 of the best count as meaning matches (calibrated
  on the demo library; ADR 0023 implementation notes).
- Verified ranks above llm_annotated at equal relevance; drafts hidden by default; a hit
  whose only keyword matches are in "don't use when" ranks last. Hits carry use/don't-use
  notes, grain, the matched terms/fields and `match` (keyword, meaning or both); scores
  have no fixed scale.
- Vectors are derived data: written on apply when the model is loaded, filled in by
  searches, backfilled by servers in the background (`search-index rebuild` by hand).
- If the model can't load, search is keyword-only and says so (a `note` on each hit);
  `CTEFORGE_SEARCH_EMBEDDINGS=off` turns meaning search off. Past questions in the
  search document: later, opt-in.

## Out of scope

- Building a full semantic layer or BI dashboards.
- Storing or caching result data.
- Hosting a SaaS (possible later as open core; ADR 0009).

## Open questions

- Grain detection: how much can be inferred automatically vs confirmed by a person?
- Join map mining: how to identify "known traps" (fan-out, many-to-many) automatically?
  (Today: relationship claims are checked against the data when proposed; traps are
  written by people and agents.)
- Resolved: search is lexical first (ADR 0017), then hybrid with meaning (ADR 0023); MCP is stdio + streamable HTTP with bearer
  tokens (ADR 0018).
