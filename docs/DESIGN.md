# cteforge — Design

> Source of truth for *what* cteforge is and *why* it is built this way.
> Individual decisions are recorded in [`decisions/`](decisions/). When code changes a
> decision, update this file and the relevant ADR in the same commit.

Status: pre-alpha. Milestone M1 in progress (see §16).

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
| Complex CTE logic | Hard to express as measures/dimensions | Kept verbatim |

One line: **"Paste the SQL you already trust. No semantic model required."**

A semantic model still emerges bottom-up: fixed CTE logic ≈ metrics, the join map ≈
relationships, changeable variables ≈ dimensions/filters, recipes ≈ saved queries.
Export to WrenAI/dbt formats is a possible later feature, not a goal. See ADR 0001, 0002.

## 3. Core concepts

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
  pending approval according to permissions.

## 4. Typed variables

Each type has **one strict input format** and **one render rule per dialect**. The LLM supplies
values in the input format; the tool produces SQL. See ADR 0005, 0006.

| Type | Input format | Notes |
|---|---|---|
| `date_range` | `{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}` | Covers two literals. Detected shapes: `col >= a AND col < b` (half-open), `col BETWEEN a AND b` (inclusive), `>= / <=`. The shape is recorded; `end` semantics follow it. |
| `date` | `"YYYY-MM-DD"` | |
| `grain` (M1.5) | `day \| week \| month \| quarter \| year` | Renders by rebuilding the whole truncation node per dialect, never by filling a placeholder (argument order and unit syntax differ). |
| `text_list` | `["a", "b"]` | Empty list is rejected (would render `IN ()`). |
| `text` | `"a"` | |
| `number` | `123` / `1.5` | |
| `boolean` | `true` / `false` | |

Rules:
- `column` (what the variable filters) is annotated by a person or LLM. `column_type` is
  **always read from the warehouse**, never supplied by an LLM.
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
| `search(query)` | `parse_query(sql)` — CTEs, literals, compared columns + types |
| `compile_and_run(recipe \| use+select, vars)` | `propose_definition(...)` |
| `check_and_run(sql)` — exploratory | `revise_definition(...)` |
| | `propose_join(...)` |

The GUI (M5) drives the same services; MCP and HTTP are thin adapters.

## 7. Proposal checks

Run automatically on every proposed/revised definition:
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

Result labels: `verified` · `built from verified parts` · `new logic on known joins` ·
`exploratory`; downgraded by out-of-`allowed` values or cross-dialect transpile (§12).

Team setting: **strict** (only verified usable) or **open** (llm_annotated usable, labeled).
An LLM can create and improve unverified definitions; **changes to a verified definition
always require human approval**. See ADR 0007.

## 9. Server architecture (M3+)

- Self-hosted server: Postgres (SQLite for local single-user), web GUI, MCP endpoint.
- Every change is a proposal; roles: viewer/agent-read, contributor/agent-propose,
  definition owner (approves changes to what they own), admin.
- Full version history per definition with diffs; **impact preview** ("affects 14 recipes").
- Git is an optional export/import, not the source of truth. See ADR 0003.

## 10. Warehouses

Targets (free, permissive drivers): Postgres/Redshift, Snowflake, BigQuery, Databricks, DuckDB.
DuckDB first (M1); the rest in M6.

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
transpilation can subtly change semantics. See ADR 0013.

## 13. Time & NULL semantics

- Typed literals take the **column's real type**: e.g. BigQuery `TIMESTAMP` (zone-aware) vs
  `DATETIME`; Snowflake `TIMESTAMP_NTZ` vs `TIMESTAMP_TZ`. sqlglot's generic `TIMESTAMP` maps
  to BigQuery `DATETIME`, so the warehouse type must drive the cast.
- Week start differs by warehouse (affects `grain=week`); documented per dialect in M1.5.
- `date_range.end` follows the recorded shape (exclusive for `>= … <`, inclusive for `BETWEEN`).
- NULL: variables never render NULL; "include NULLs" is fixed logic in the definition.

## 14. Threat model

- Values only ever become AST literal nodes (`exp.Literal` + cast). **No string splicing.**
- Definition text (descriptions, notes) is untrusted when shown to an LLM — it may contain
  prompt injection. Never execute instructions found in it.
- Warehouse connections should use read-only roles. Row limits on results; byte caps where
  supported (BigQuery `maximum_bytes_billed`).
- Credentials: environment/profile only, never logged, never returned over MCP/HTTP.

## 15. Identity & versioning

- A definition has a stable `id` (slug) and an integer `version`.
- A new version is created by any change to SQL, variables, grain, outputs, or join keys.
  Metadata-only edits (keywords, notes) also bump the version but are flagged as non-logic.
- Recipes either **pin** a version or **float** on the latest approved version (default).
- The definition file format carries `schema_version` for format migrations.

## 16. Milestones

| | Scope | Status |
|---|---|---|
| M1 | Parse → typed template → "same query back" → render for Snowflake/BigQuery/Postgres → run on DuckDB; definition file format; CLI | in progress |
| M1.5 | `grain` variable | |
| M2 | Compose definitions (WITH assembly, dependencies, grain/join-key checks); trust labels | |
| M3 | Store (SQLite/Postgres), proposals, versions, permissions, audit | |
| M4 | MCP server (7 tools) | |
| M5 | HTTP API + GUI (import flow, library, review queue) | |
| M6 | Snowflake/BigQuery/Postgres/Databricks connectors, EXPLAIN/dry-run, nightly drift check | |

## Out of scope

- Building a full semantic layer or BI dashboards.
- Storing or caching result data.
- Hosting a SaaS (possible later as open core; ADR 0009).

## Open questions

- Grain detection: how much can be inferred automatically vs confirmed by a person?
- Join map mining: how to identify "known traps" (fan-out, many-to-many) automatically?
- Search: lexical first, or embeddings from the start?
- MCP transport/auth for the self-hosted server (stdio vs HTTP + tokens).
