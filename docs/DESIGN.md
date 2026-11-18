# cteforge — Design

> Source of truth for *what* cteforge is and *why* it is built this way.
> Individual decisions are recorded in [`decisions/`](decisions/). When code changes a
> decision, update this file and the relevant ADR in the same commit.

Status: pre-alpha. M1–M5 built and wired end to end (`cteforge demo` → `cteforge serve`: GUI, HTTP API, MCP over
stdio and streamable HTTP); M6 connectors built; DuckDB and ClickHouse are verified against real engines (ClickHouse
locally, not yet in CI); the others are unverified against real warehouses (see §16).

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
relationships, changeable variables ≈ dimensions/filters, recipes ≈ saved queries.
Export to WrenAI/dbt formats is a possible later feature, not a goal. See ADR 0001, 0002.

## 3. Core concepts

> **Planned (ADR 0026, accepted, not built):** recipes become **composed definitions** (a
> definition whose body is parts + a final select, parts referenced live); the join map
> stays mined from definitions, and `propose_join` gains `annotate` / `add` (agent-added
> edges never auto-apply). Library and Review become one GUI page.

- **Definition** — one trusted CTE plus: title, plain-language meaning, keywords/synonyms,
  "use when", "don't use when", source dialect, tables read, output columns, **grain**
  (e.g. one row per account per month), join keys, variables, state, verifier + date, version.
- **Variable** — a typed hole in a definition's SQL (§4), with a role: `changeable` or `fixed`.
- **Recipe** — a saved query expressed as definitions + variable values + an optional final
  `SELECT`. Not a copy of SQL: fixing a definition fixes every recipe that uses it.
- **Join map** — join edges mined from trusted SQL: tables, keys, relationship type,
  usage count, known traps. A reference for exploration, not a restriction.
- **Trust label** — attached to every result (§8).
- **Proposal** — any change (new/edited definition, recipe, join) from a person or an agent,
  pending approval according to permissions (§18).
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

> **Planned (ADR 0026, 0027, accepted, not built):** `propose_recipe` / `list_recipes` /
> `recipe_id` go; `propose_definition` takes `sql` or `composition`; `propose_join` takes
> `action: add | annotate`. Review (`list_proposals`, `review_proposal`) and admin
> (`list_users`, `manage_user`, `manage_role`) tools are added, and `tools/list` is
> filtered per token. Admin agents may verify (four-eyes still applies).

| Use the library | Build the library |
|---|---|
| `search(query)` — ranked definitions + their variables | `parse_query(sql, dialect?)` — literals with IDs, columns + types, structural facts, range/grain suggestions, joins |
| `get_definition(id)` — full card: meaning, variables with input formats, outputs, grain | `propose_definition(sql, dialect?, metadata, annotations)` (an omitted `dialect`: §12) |
| `compile_and_run(uses, values, select?)` or `(recipe_id, values)` | `revise_definition(id, base_version, sql?, metadata?, annotations?)`; with `new_id` alone it proposes a rename (ADR 0024) |
| `check_and_run(sql)` — exploratory, labeled; SQL that is a definition's original query gets a `matches_definition` hint to use `compile_and_run` instead | `propose_join(...)` · `propose_recipe(...)` (`version`, `state`, `schema_version` are set by cteforge, never by the caller) |
| `list_recipes(query?)` · `list_joins(table?)` · `describe_tables(tables?)` — discovery: saved recipes, join-map keys, columns + types (+ units the library declares) | |

Approving and verifying are not MCP tools: they happen in the GUI, HTTP API or CLI by a
person (ADR 0007, 0018). The GUI (M5) drives the same services; MCP and HTTP are thin adapters.

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
| `verified` | Approved by a person | Yes, labeled with verifier + date |

Result labels, best to worst: `verified` · `built_from_verified_parts` · `llm_annotated` ·
`new_logic_on_known_joins` · `exploratory`. Policy (trust/, pure):
- One verified definition, no final SELECT → `verified`. A final SELECT over verified
  definitions joining only on known keys → `built_from_verified_parts`.
- Any `llm_annotated` definition caps the label at `llm_annotated`.
- Raw SQL (`check_and_run`) → `new_logic_on_known_joins` if every join matches the join map,
  else `exploratory`. Joins in a final SELECT that aren't known → `exploratory`.
- Each downgrade (fixed-variable override, out-of-`allowed` value, cross-dialect transpile)
  lowers the label one step.
- Drafts are never usable.

Team setting: **strict** (only verified usable) or **open** (llm_annotated usable, labeled).
An LLM can create and improve unverified definitions; **changes to a verified definition
always require human approval**. See ADR 0007.

## 9. Server architecture (M3+)

> **Planned (ADR 0027, accepted, not built):** configurable roles (Allowed = ceiling, On by
> default), many named show-once personal access tokens per user with per-token tool
> lists enforced in services; agents never see tokens; admin agents manage non-admin
> users and roles. Sign-in roadmap: tokens now → optional OIDC GUI sign-in → optional
> MCP OAuth with cteforge as its own authorization server.

- Self-hosted server: Postgres (SQLite for local single-user), web GUI, MCP endpoint.
- Every change is a proposal; roles: viewer/agent-read, contributor/agent-propose,
  definition owner (approves changes to what they own), admin.
- Full version history per definition with diffs; **impact preview** ("affects 14 recipes").
- Git is an optional export/import, not the source of truth. See ADR 0003.
- Users and settings: team settings (strict/open mode, **default connection**) and
  users/roles/tokens are admin-only. The default connection decides the default dialect:
  the dialect follows the connection (ADR 0021). Resolution: the admin's setting →
  `CTEFORGE_DEFAULT_PROFILE` → the only profile → none.

## 10. Warehouses

Targets (free, permissive drivers): Postgres, Redshift (its own dialect, ADR 0022), Snowflake,
BigQuery, Databricks, ClickHouse (ADR 0025), DuckDB. A team may connect several; each definition maps to one
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

Used for: column types, compile checks (EXPLAIN / BigQuery dry run), running queries,
and **nightly re-validation** of every definition to detect schema drift. Credentials stay
on the self-hosted server. See ADR 0004.

## 11. Audit (M3)

cteforge logs **its own** tool calls: question, definitions + versions, variable values, trust
label, result metadata (row count, duration, accepted/rejected). It does **not** duplicate the
warehouse's query history and does **not** store result rows by default.

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
  `STATEMENT_TIMEOUT`, ClickHouse `max_execution_time` per request; DuckDB's is checked against a real engine
  in CI, ClickHouse's locally by the live tests). There is no cancel on client disconnect yet, and a wide
  result is still fetched in full by the connector (ClickHouse streams and stops after the
  row limit).
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
- Recipes either **pin** a version or **float** on the latest approved version (default).
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
| M5 | HTTP API + GUI (import flow, library, revise, explore: recipes/compose/join map/tables/SQL, review queue) | done; GUI↔API contract e2e-tested |
| M6 | Snowflake/BigQuery/Postgres/Databricks connectors, EXPLAIN/dry-run, statement timeouts, drift check | built; DuckDB verified against a real engine in CI, ClickHouse locally (22 live tests, ClickHouse 26.10, 2026-11-17). The other connectors (incl. their timeouts and read-only setup) are unit-tested against fake drivers only and unverified against real warehouses |

## 17. Composition (M2)

A recipe (or an ad-hoc `compile_and_run`) names definitions by alias, gives values per alias,
and optionally a final SELECT written by the agent over those aliases.
- Each definition is rendered on its own (templating), then compose **hoists** its inner CTEs
  into one top-level WITH, renaming them `<alias>__<cte>` to avoid collisions, and adds the
  definition's final SELECT as a CTE named `<alias>`. Order follows dependencies.
- The final SELECT must be a single read-only SELECT that references only the aliases (and,
  with a lower label, raw tables). Without one, a single definition is returned as-is.
- Checks: every alias resolves; join conditions between aliases use declared `join_keys`
  (else an `unknown_join` issue); joining definitions of different grain without
  aggregation raises a `fan_out_risk` warning.

## 18. Proposal & review flow (M3)

- Every change is a proposal: `new_definition`, `revise_definition`, `new_recipe`, `new_join`,
  `rename_definition` (ADR 0024: from/to + the references seen when proposed; no checks to
  run; never "approve with verify"; another rename of the same definition that was
  overtaken is refused as stale).
  Checks (§7) run at proposal time and are stored with it; the GUI shows them with a diff and
  an impact preview (recipes using the definition).
- Auto-apply: in **open** mode, a proposal whose checks all pass and that targets a new or
  unverified definition is applied immediately as `llm_annotated`. Everything else waits.
- Changes to a **verified** definition always wait for a person (approver or the
  definition's owner). Approving can also verify (human only). Rejections keep a comment.
- **Four-eyes rule** (ADR 0018 addenda): nobody may approve their own proposal or verify
  a definition version they authored any part of, unless they are a **human** admin (so
  single-user local mode keeps working). An agent admin never approves its own proposals
  and never verifies. A version's authors are the author of the newest
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
- Resolved: search is lexical first (ADR 0017), then hybrid with meaning (ADR 0023); MCP is stdio + streamable HTTP with bearer
  tokens (ADR 0018).
