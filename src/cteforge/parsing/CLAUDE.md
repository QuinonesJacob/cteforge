# src/cteforge/parsing/

Reads SQL and reports its structure. Pure. Project root: ../../../CLAUDE.md

## What belongs here (authoritative)
- `analyze.py` — analyze(sql, dialect, schema) → QueryAnalysis: CTEs, tables, joins, output
  columns, filter literals with stable IDs, the column each literal is compared to (through
  wrappers, CTEs and derived tables), structural context facts, and suggested range pairs.
  Also `parse`, `scopes`, `find_literals` / `FoundLiteral` and `wrapper_sql`, shared with
  templating so both sides agree on IDs.
- `types.py` — warehouse type string → VariableType candidates + sqlglot DataType.

If something does not clearly fit one of the above, it does not belong here.
"Not forbidden" is not the same as "allowed."

## How analyze.py works (keep in sync with the code)
- **Scopes.** Each top-level CTE (named as written), then the outer query. The outer query
  is `main`; if a CTE already has that name (case-insensitive) it becomes the first free
  name of `main_2`, `main_3`, ... (so a CTE called `main` never collides with it).
- **Literal IDs** are `<scope>.L<n>`: n counts filter literals in depth-first, pre-order,
  argument order within the scope. IDs do NOT include the operator. A filter literal climbs
  only through value-side wrappers (functions, arithmetic, `||`, parentheses, INTERVAL) to a
  comparison (`= <> > >= < <= LIKE ILIKE SIMILAR TO`, `IS [NOT] DISTINCT FROM`), `IN (...)`
  list item, or BETWEEN low/high; its operand must hold no column and the other side must
  (column, aggregate, window or subquery). Literals on the column side
  (`DATE_TRUNC('month', col)`, `ROUND(x, 2)`), `format` arguments and date-part `unit`
  arguments (incl. the first argument of unparsed `DATE_PART('year', ...)`, `DATEADD`, ...)
  are never IDs. The node replaced by templating is the Cast for a typed literal
  (`TIMESTAMP '...'`, `CAST('...' AS T)`), the Neg for `-5`, else the Literal.
- **List shapes**: each element is its own literal, compared to the column side.
  `x = ANY(ARRAY[...])`, `d ILIKE ANY ('a%', ...)`, `x <> ALL(...)` → operator `= ANY`,
  `ILIKE ANY`, `<> ALL`; BigQuery `x IN UNNEST([...])` → `IN UNNEST`; row values
  `(a, b) = (1, 2)` / `(a, b) IN ((1, 2), ...)` → `ROW =` / `ROW IN`, each element compared
  to the matching element of the column-side row (row `<`/`>` are lexicographic, hence ROW).
  None of these form IN groups (templating's text_list replaces plain IN lists only).
- **IN groups** are `<scope>.IN<n>`, numbered per scope in ID order (operator `IN` only).
- **Context facts** (ADR 0015), computed from the predicate up to the scope root:
  `negated` (any NOT, or a predicate's own `negate` flag, e.g. DuckDB `NOT LIKE`),
  `or_branch`, `case_condition` (CASE WHEN / IF condition, aggregate FILTER (WHERE ...)),
  `join_on` / `outer_join_on` (ON of an inner or semi / LEFT-RIGHT-FULL join; an anti
  join's ON is `negated` + `join_on`, like NOT EXISTS), `having`, `subquery`
  (scalar, IN, EXISTS, derived table, nested CTE), `value_wrapped`. Facts only, in enum order.
- **Value-side wrappers** (ADR 0014): `wrapper_sql` is the predicate operand with this
  literal shown as `?`. A literal directly in CAST stays TYPED and is not value_wrapped.
  A `format` argument of a date-parsing function (StrToTime, StrToDate, TsOrDsToDate, ...)
  sets `suggested_format` on the value literal: sqlglot stores formats as Python strftime;
  it is kept only if it parses the original value.
- **Column-side wrappers**: the column is resolved through the wrapper (`wrapped=True`).
  `compared_type` is the column's warehouse type when unwrapped; otherwise sqlglot
  `annotate_types` on the expression with each typed column swapped for
  `CAST(NULL AS <type>)` (annotate_types forgets types set on bare columns), falling back to the column
  type when every wrapper keeps the type (COALESCE, ROUND, DATE_TRUNC, arithmetic on numbers...).
  Derived CTE columns are typed from their computing expression. Types are memoized per
  node within one analysis, so layered CTEs (`x + x + x` per layer) cost linear time.
- **CTE visibility** is positional: a 1-part table name is a CTE only in the outer query
  and in CTEs defined after it; in its own body or an earlier CTE it is the base table
  (`WITH deals AS (SELECT * FROM deals)` reads table `deals`). WITH RECURSIVE: all visible.
- **Range candidates**: a lower (`>`/`>=`) and upper (`<`/`<=`) bound pair only when both are
  direct operands of the same AND chain (nested ANDs and parentheses flattened; NOT / OR
  stop the chain) on the same column expression, each bound holding exactly one literal and
  exactly one low + one high in that chain; or one BETWEEN. Shapes: `>= <` half_open,
  `>= <=` / BETWEEN inclusive, `> <` exclusive, `> <=` start_exclusive.
- **Schema lookups**: keys `table`, `schema.table`, `db.schema.table`, any case. Unquoted
  identifiers match case-insensitively, quoted ones exactly. The fully qualified key wins,
  else the single key whose overlapping trailing parts match. Table names in results are
  qualified as written with unquoted parts lower-cased (`analytics.deals`).
- **Joins**: per JOIN, sources resolved through aliases to base tables / CTE names /
  derived-table aliases; keys are column = column pairs in the ON's top-level AND chain
  (or USING columns), oriented (left, right). `join_type`: semi / anti win over the side
  (`LEFT SEMI JOIN` → semi), then left / right / full, cross, else inner.

## Common misplacements
- Producing templates or new SQL — templating/.
- Querying the warehouse for schema — services/ fetches it via warehouse/ and passes it in.

## Interactions
Imports domain. Imported by templating, compose, services.

## If you need to add something not on the allowlist
STOP. Do not place the file here on the assumption it kind of fits. Surface the case to
the user, propose the addition, and update this CLAUDE.md (and the root CLAUDE.md if the
change is project-wide) before placing the new code. The architecture evolves through
conscious decisions, not silent drift.
