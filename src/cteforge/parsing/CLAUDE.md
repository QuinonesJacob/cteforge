# src/cteforge/parsing/

Reads SQL and reports its structure. Pure. Project root: ../../../CLAUDE.md

## What belongs here (authoritative)
- `analyze.py` — analyze(sql, dialect, schema) → QueryAnalysis: CTEs, tables, joins, output
  columns, filter literals with stable IDs, the column each literal is compared to (through
  wrappers, CTEs and derived tables), structural context facts, suggested range pairs, and
  grain candidates (date truncations, `QueryAnalysis.grains`, M1.5).
  Also `parse` (raises SqlParseError with `syntax_error_message`: "SQL syntax error at
  line L, column C: <cause>"; sqlglot's tokenizer position is unreliable, so unclosed
  quotes / comments are located by `_unclosed_opener`, preferring a quoted span that runs
  across a line break), `transpile` (one statement from a source to a target dialect),
  `generate` (tree → SQL text; EVERY SQL text cteforge writes goes through it: in ClickHouse
  it writes toStartOf* / toMonday back under their own names instead of sqlglot's
  `dateTrunc`, and date_trunc / dateTrunc with the name written and a lower-case unit; in
  MySQL it writes a `TIMESTAMP '...'` literal as a literal, not the TIMESTAMP() function), `reject_query_parameters` (ClickHouse `{x:Type}` / `?` in trusted SQL →
  SqlParseError), `reject_mysql_hidden_sql` (run by `parse` for MySQL: executable comments
  `/*! ... */` / `/*M! ... */` and MAX_EXECUTION_TIME / SET_VAR hints → SqlParseError;
  `parse` also reads MySQL `INTERVAL (expr) unit` like `INTERVAL expr unit`),
  `mysql_period` / `MySQLPeriod` / `MYSQL_PERIOD_FORMATS` (MySQL grain candidates), `is_truncation` / `truncated_expression`, `lambda_binding` /
  `array_join_binding` (ClickHouse element bindings), `CLICKHOUSE_*` names, `scopes`, `find_literals` / `FoundLiteral`, `wrapper_sql`, `find_grains` /
  `FoundGrain`, `truncation_unit`, `identifier_units`, `COLUMN_UNIT_PREFIX` and
  `TRUNCATIONS`, shared with templating so both sides
  agree on IDs.
- `types.py` — warehouse type string → VariableType candidates + sqlglot DataType.
  Text includes BPCHAR; Redshift SUPER / VARBYTE / GEOMETRY / HLLSKETCH map to no
  variable type (not filterable by a typed value). ClickHouse: LowCardinality(T) /
  SimpleAggregateFunction(f, T) unwrap to T (Nullable is a flag in sqlglot); FixedString /
  Enum8 / Enum16 / UUID are text in ClickHouse only; Array / Map / Tuple / JSON / Nested
  have no variable type. `element_type` gives an Array's element / a Map's value type.
  MySQL only (ADR 0028): TINYINT(1) → number + boolean, ENUM / SET → text, YEAR → number;
  JSON / BLOB / BINARY / GEOMETRY / spatial → none (UNSIGNED types are numbers in sqlglot).

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
- **ClickHouse shapes** (only when the dialect is ClickHouse, so other dialects' literal IDs
  never shift; `find_literals(tree, dialect)`): `has(arr, 'x')` → `HAS`; `hasAny(arr,
  [...])` / `hasAll` → `HAS ANY` / `HAS ALL` (list shapes like `= ANY`, no IN group);
  `match(s, 're')` → `MATCH`. `compared_type` of HAS* is the array's element type. A lambda
  parameter (`arrayExists(x -> x = 'a', arr)`; parameter i ↔ the i-th non-lambda argument)
  counts as the column side and resolves to its array column, typed as the element; an
  unqualified `ARRAY JOIN` name likewise. `map['k']` / `arr[i]` → value / element type;
  toDate / toDateTime* / toString / toStartOf* / JSONExtractString have their ClickHouse
  result types. Arguments after the value of toDate / toDateTime64 / toDecimal* /
  parseDateTime*BestEffort (scale, time zone) are never literals. ARRAY JOIN is not a join.
  A scalar `WITH 'x' AS m` is a scope with no outputs and no filter literals.
- **IN groups** are `<scope>.IN<n>`, numbered per scope in ID order (operator `IN` only).
- **Context facts** (ADR 0015), computed from the predicate up to the scope root:
  `negated` (any NOT, a predicate's own `negate` flag, e.g. DuckDB `NOT LIKE`, or an
  excluding operator: `<>` / `!=`, `IS DISTINCT FROM`, `<> ALL`, `ROW <>`),
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
- **Grain candidates** (M1.5): every DateTrunc / TimestampTrunc / DatetimeTrunc node (what
  sqlglot makes of DATE_TRUNC, TRUNC(d, fmt), TIMESTAMP_TRUNC, DATETIME_TRUNC, and
  ClickHouse toStartOfDay / Month / ... / toMonday / dateTrunc) gets `<scope>.G<n>`; in
  ClickHouse also the Anonymous `toStartOfWeek(x[, mode[, tz]])` (even / no mode →
  `week(sunday)`, odd → `week`) and `toStartOfInterval(x, INTERVAL n unit[, ...])` (`unit`
  when n = 1 without origin, else e.g. `'15' minute`); in MySQL `DATE_FORMAT(x, f)` with
  f = '%Y-%m-%d' / '%Y-%m-01' / '%Y-01-01' (+ optional ' 00:00:00') → day / month / year,
  and the day format over `x - INTERVAL WEEKDAY(x) DAY` / `DATE_SUB(x, INTERVAL WEEKDAY(x)
  DAY)` → week or `MAKEDATE(YEAR(x), 1) + INTERVAL QUARTER(x) - 1 QUARTER` → quarter
  (`mysql_period`; `truncated_expression` is x; DATE(x), label formats and values already
  aligned with a calendar-part function — YEAR, WEEKDAY, MAKEDATE, ... — are not candidates). n counts truncations in the same walk as literal IDs,
  wherever they sit (SELECT list, filters, nested). `GrainCandidate`: `unit` (lower case,
  abbreviations spelled out: `MM` → month, `Q` → quarter; BigQuery `WEEK` / `WEEK(SUNDAY)`
  → `week`, other week starts → `week(<day>)`, `ISOWEEK` → `isoweek`), `column_sql` (the
  truncated expression), `sql`, `column` / `column_type` (lineage as for literals),
  `output` (the alias when it is a projection's value), and the literal context facts.
  The unit argument is never a literal ID; a literal inside a value-side truncation still is.
  A unit read from a column is `column <name>` (never a grain value): a qualified Column
  (`cfg.month`) from the tree; a bare or double-quoted name in DuckDB / Postgres /
  Redshift / Databricks (where the unit is a string expression) from the SQL's tokens
  (`identifier_units`), because sqlglot parses it like the string. A name used both ways
  in one query marks every truncation to that unit (conservative). `COLUMN_UNIT_PREFIX`.
- **Joins**: per JOIN, sources resolved through aliases to base tables / CTE names /
  derived-table aliases; keys are column = column pairs in the ON's top-level AND chain
  (or USING columns), oriented (left, right). `join_type`: semi / anti win over the side
  (`LEFT SEMI JOIN` → semi), then left / right / full, cross, else inner. `in_subquery`
  (ADR 0029): the join's SELECT is nested in a subquery / derived table / nested CTE
  under its scope's root (a UNION branch of the scope's own query is not).

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
