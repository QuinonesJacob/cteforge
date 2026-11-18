# src/cteforge/compose/

Combines rendered definitions and an optional final SELECT into one query (DESIGN §17). Pure.
Project root: ../../../CLAUDE.md

## What belongs here (authoritative)
- `assemble.py` — `compose()`: CTE hoisting/renaming, WITH assembly, dependency order.
- `checks.py` — final-SELECT validation, join-key and grain (fan-out) checks between parts.
- `relationship.py` — ADR 0026: the counting query that checks a join's relationship claim
  (`relationship_query(table, keys, dialect)`: rows with every key set vs distinct key
  tuples, built from sqlglot identifiers only — no values, no text splicing; a table name
  has 1–3 dotted parts, a key one column), and the pure rules `side`, `relationship_of`,
  `claimed_sides`, `consistent` (every side the claim calls "one" is unique in the data).

If something does not clearly fit one of the above, it does not belong here.
"Not forbidden" is not the same as "allowed."

## Public API
```python
class Part(BaseModel):            # frozen
    alias: Ident                  # name the final SELECT uses
    definition: Definition        # for grain / join_keys / outputs
    sql: str                      # this definition already rendered (templating) in `dialect`
class JoinUse(BaseModel):         # frozen; a join written in the final SELECT
    left: str; right: str         # alias or raw table name
    keys: list[tuple[str, str]]   # (left_col, right_col) equality pairs
    known: bool                   # both sides aliases; each pair is the SAME key in both join_keys
class ComposedQuery(BaseModel):   # frozen
    sql: str
    dialect: Dialect
    issues: list[Issue]
    raw_tables: list[str]         # tables the final SELECT reads that aren't aliases, plus
                                  # `name()` for table functions (read_csv, dblink, RESULT_SCAN)
    joins: list[JoinUse]
def compose(parts: list[Part], select: str | None, dialect: Dialect) -> ComposedQuery
```
Rules: no select → exactly one part, returned unchanged. With a select: each part's inner
CTEs are hoisted into one top-level WITH as `<alias>__<cte>` (references rewritten), the
part's final query becomes CTE `<alias>`, then the final SELECT. The select must be ONE
SELECT/UNION statement (anything else → RenderError). Unused aliases → warning; references
to unknown names that aren't tables → `unknown_alias` error. `unknown_join` warning for joins
whose key pairs aren't the same declared `join_keys` column on both sides; `fan_out_risk`
warning when parts of different `grain` are joined directly (aggregating after the join does
not silence it). All parts must be in `dialect`.

Exact rules (details in the module docstrings of assemble.py / checks.py):
- Names compare case-insensitively. Only unqualified table refs can name a CTE or alias.
  A top-level CTE is visible to the main query and LATER CTEs only (a CTE's own body or an
  earlier CTE reading that name reads the real table); a WITH nested in a subquery shadows,
  with the same rule (not inside that nested CTE's own body or earlier nested CTEs).
- Name clashes are avoided with a `_2`, `_3`… suffix (hoisted names, the final SELECT's own
  CTEs). RenderError only when unavoidable: an alias equal to an unqualified table a part
  reads, a final-SELECT CTE named like an alias, or two CTEs of one WITH whose names differ
  only in case. Also RenderError: unparseable SQL, duplicate aliases, WITH RECURSIVE anywhere,
  any non-read-only node (DML/DDL, data-modifying CTE, SELECT INTO, FOR UPDATE), a call to a
  denylisted side-effect function (`checks._SIDE_EFFECT_FUNCTIONS`: nextval, set_config,
  pg_terminate_backend, dblink, pg_read_file, query_to_xml / table_to_xml / schema_to_xml /
  ts_stat (read a table by name or run a SQL string), ClickHouse joinGet, getenv, DuckDB
  query(), Snowflake SYSTEM$..., MySQL LOAD_FILE / SLEEP / BENCHMARK / GET_LOCK / ...;
  not exhaustive — warehouses still need read-only roles), more than one statement.
- Table functions in FROM/JOIN anywhere in the final SELECT are raw reads (`raw_tables`,
  JoinUse side `name()`), except row generators (UNNEST, GENERATE_SERIES, range, ...).
- `unknown_alias` (error) only for a qualified column `x.col` whose `x` is no visible source
  (sources of its scope; subqueries/derived tables also see enclosing queries). Unknown FROM
  names are raw tables. `x` equal to a part output column is treated as struct access.
- `unused_alias` (warning): a part alias never read by the final SELECT (it is still emitted).
- Joins: every SELECT in the final query (outer first). Keys from ON equalities (AND chain),
  USING, or — for comma/cross joins — WHERE equalities. Unqualified key columns resolve via
  part `outputs` when exactly one side has them. `known`: both sides are parts and every
  pair is (k, k) with k in both sides' `join_keys` (`a.account_id = b.user_id` is unknown).
  `fan_out_risk`: both sides are parts read directly with non-empty, different grain
  (whitespace/case-normalized), whether or not the SELECT aggregates afterwards — SUM over
  the coarser side is still inflated. Pre-aggregating one side in a subquery/CTE makes it
  not a part, which silences it. One warning per pair.

## Common misplacements
- Rendering a single definition — templating/. Choosing trust labels — trust/.
- Checking joins against the stored join map — services/ (uses `raw_tables`/`joins`).

## Interactions
Imports domain, parsing, templating. Imported by services.

## If you need to add something not on the allowlist
STOP. Do not place the file here on the assumption it kind of fits. Surface the case to
the user, propose the addition, and update this CLAUDE.md (and the root CLAUDE.md if the
change is project-wide) before placing the new code. The architecture evolves through
conscious decisions, not silent drift.
