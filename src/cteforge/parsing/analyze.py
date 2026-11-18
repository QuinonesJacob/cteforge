"""Analyze a trusted query: CTEs, tables, joins, outputs, and the literals that could become
variables — with the column each literal filters (through wrappers and CTEs), structural
facts about where each literal sits, and suggested range pairs (ADR 0014, 0015).

Pure. The schema (table → column → type string) is fetched by services/ and passed in.

Scopes: each top-level CTE (named as written), then the outer query. The outer query is
`main`; if a CTE already has that name (case-insensitive) it is the first free name of
`main_2`, `main_3`, ... so IDs never collide.

Literal IDs are `<scope>.L<n>`: n counts filter literals in depth-first, pre-order,
argument order within the scope. A filter literal is one whose path up to a comparison /
IN / BETWEEN / LIKE goes only through value-side wrappers (functions, arithmetic,
concatenation, parentheses) and list containers (ANY/ALL arrays, IN UNNEST, row values),
and whose operand holds no column, while the other side does. Format and date-part unit
arguments of functions are never literals. IDs are stable for unchanged SQL and are what
annotations refer to. `find_literals` is shared with templating so both sides agree.

CTE names are positional: a CTE's own body and earlier CTEs read the base table of that
name (WITH RECURSIVE: every CTE is visible everywhere).

Grain IDs are `<scope>.G<n>`: n counts date truncation nodes (DateTrunc, TimestampTrunc,
DatetimeTrunc) in the same walk order, wherever they sit. `find_grains` is shared with
templating, which rebuilds the named nodes for a `grain` variable (M1.5). ClickHouse adds
toStartOfWeek / toStartOfInterval (ADR 0025), MySQL DATE_FORMAT(x, '<period start>')
(ADR 0028). A unit argument
that is a column, not a unit keyword or string, is reported as `column <name>` (never a
grain value): sqlglot keeps `cfg.month` as a Column, but reads a bare `month` as the unit
keyword even where it is a column (DuckDB, Postgres, Databricks), so `analyze` finds those in
the SQL's tokens (`identifier_units`).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

import sqlglot
from pydantic import BaseModel, ConfigDict
from sqlglot import exp
from sqlglot.errors import ParseError, SqlglotError, TokenError
from sqlglot.optimizer.annotate_types import annotate_types
from sqlglot.tokens import Token, TokenType

from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import SqlParseError
from cteforge.domain.models import LiteralForm
from cteforge.domain.variables import RangeShape
from cteforge.parsing.types import data_type, element_type

# Table key → column → warehouse type. Keys: "table", "schema.table" or "db.schema.table",
# any case (matching rules: SchemaIndex).
Schema = dict[str, dict[str, str]]
MAIN_SCOPE = "main"

_COMPARISONS: dict[type[exp.Expression], str] = {
    exp.EQ: "=",
    exp.NEQ: "<>",
    exp.GT: ">",
    exp.GTE: ">=",
    exp.LT: "<",
    exp.LTE: "<=",
    exp.Like: "LIKE",
    exp.ILike: "ILIKE",
    exp.NullSafeEQ: "IS NOT DISTINCT FROM",
    exp.NullSafeNEQ: "IS DISTINCT FROM",
    exp.SimilarTo: "SIMILAR TO",
}
_PREDICATES = (*_COMPARISONS, exp.In, exp.Between)


# Operators that exclude the value (`<>`, `IS DISTINCT FROM`, `<> ALL`, `ROW <>`): the
# literal names what is filtered OUT, as under a NOT.
def _excluding(operator: str) -> bool:
    op = operator.removeprefix("ROW ")
    return op in ("<>", "IS DISTINCT FROM") or op.startswith("<> ")


_FLIP = {">": "<", ">=": "<=", "<": ">", "<=": ">="}
_LOW_OPS = {">", ">="}
_HIGH_OPS = {"<", "<="}
_SHAPES = {
    (">=", "<"): RangeShape.HALF_OPEN,
    (">=", "<="): RangeShape.INCLUSIVE,
    (">", "<"): RangeShape.EXCLUSIVE,
    (">", "<="): RangeShape.START_EXCLUSIVE,
}
# Value-side wrappers a literal may sit in (minus boolean logic and predicates).
_WRAPPERS = (exp.Func, exp.Binary, exp.Paren, exp.Neg, exp.Interval)
_NOT_WRAPPERS = (exp.Predicate, exp.Connector, exp.Not, exp.Query)
# Arguments that are never values: format strings, date-part units, parser-injected defaults.
_NON_VALUE_ARGS = {"format", "default_year", "unit"}
# Functions sqlglot leaves unparsed (Anonymous) whose first argument is a date-part unit.
_UNIT_FIRST_FUNCS = {
    "DATE_PART",
    "DATEPART",
    "DATE_TRUNC",
    "DATETRUNC",
    "DATEADD",
    "DATEDIFF",
    "TIMESTAMPADD",
    "TIMESTAMPDIFF",
}
# Quantifiers some dialects parse as plain functions (`x <> ALL(ARRAY[...])`).
_QUANTIFIER_FUNCS = {"ANY", "SOME", "ALL"}
# Date-parsing functions whose `format` argument describes their `this` argument.
_PARSE_FUNCS = (
    exp.StrToTime,
    exp.StrToDate,
    exp.TsOrDsToDate,
    exp.TsOrDsToTimestamp,
    exp.TsOrDsToDatetime,
)
# Nodes that make an expression depend on rows (a "column side").
_REFERENCES = (exp.Column, exp.AggFunc, exp.Window, exp.Subquery, exp.Select)
# Column-side wrappers whose result keeps the column's type (fallback when annotation fails).
_TYPE_PRESERVING = (
    exp.Paren,
    exp.Neg,
    exp.Abs,
    exp.Coalesce,
    exp.Round,
    exp.Floor,
    exp.Ceil,
    exp.Trim,
    exp.Upper,
    exp.Lower,
    exp.DateTrunc,
    exp.TimestampTrunc,
)
_ARITHMETIC = (exp.Add, exp.Sub, exp.Mul, exp.Div)
_MAX_DEPTH = 64  # Lineage hops (CTE layers); types are memoized, so cost stays linear.
# Date truncation nodes: grain candidates (M1.5). sqlglot normalizes DATE_TRUNC / TRUNC /
# TIMESTAMP_TRUNC / DATETIME_TRUNC of every supported dialect into one of these.
TRUNCATIONS = (exp.DateTrunc, exp.TimestampTrunc, exp.DatetimeTrunc)
# Unit spellings beyond sqlglot's own abbreviations (TimeUnit.UNABBREVIATED_UNIT_NAME).
_UNIT_ALIASES = {
    "DD": "DAY",
    "DAYS": "DAY",
    "WK": "WEEK",
    "WEEKS": "WEEK",
    "MM": "MONTH",
    "MON": "MONTH",
    "MONS": "MONTH",
    "MONTHS": "MONTH",
    "QTR": "QUARTER",
    "QUARTERS": "QUARTER",
    "YY": "YEAR",
    "YYYY": "YEAR",
    "YEARS": "YEAR",
}
# Dialects whose truncation unit is a string expression, so an unquoted (or double-quoted)
# name there is a column; sqlglot still reads a 1-part name as the unit keyword. Function
# name → position of the unit argument. Snowflake and BigQuery take unit keywords.
_STRING_UNIT_ARGS: dict[Dialect, dict[str, int]] = {
    Dialect.DUCKDB: {"DATE_TRUNC": 0, "DATETRUNC": 0},
    Dialect.POSTGRES: {"DATE_TRUNC": 0},
    Dialect.REDSHIFT: {"DATE_TRUNC": 0},
    Dialect.DATABRICKS: {"DATE_TRUNC": 0, "TRUNC": 1},
}
COLUMN_UNIT_PREFIX = "column "  # `GrainCandidate.unit` of a truncation to a column's value.

# ── ClickHouse (ADR 0025) ──
# Array membership / regex predicates, ClickHouse only (in other dialects these nodes were
# never filter predicates, and adding them there would renumber stored literal IDs):
# `has(arr, 'x')` → HAS (the literal is an element sought in the array column),
# `hasAny(arr, ['a', 'b'])` / `hasAll(...)` → HAS ANY / HAS ALL (list shapes like `= ANY`:
# each element its own literal, no IN group), `match(s, 're')` → MATCH.
_CLICKHOUSE_LIST_FUNCS = {"hasAny": "HAS ANY", "hasAll": "HAS ALL"}
_ELEMENT_OPERATORS = {"HAS", "HAS ANY", "HAS ALL"}  # compared with the array's element type
# toStartOfWeek(x[, mode[, tz]]) and toStartOfInterval(x, INTERVAL n unit[, ...]) stay
# Anonymous in sqlglot; they are grain candidates in ClickHouse. toStartOfDay / Month /
# Quarter / Year / Hour / Minute and toMonday parse as TimestampTrunc (sqlglot keeps the
# name in meta, `CLICKHOUSE_NAME_KEY`) and date_trunc / dateTrunc as DateTrunc.
CLICKHOUSE_WEEK_FUNC = "toStartOfWeek"
CLICKHOUSE_INTERVAL_FUNC = "toStartOfInterval"
CLICKHOUSE_NAME_KEY = "clickhouse_name"

# ── MySQL (ADR 0028) ──
# MySQL has no DATE_TRUNC. `DATE_FORMAT(x, f)` with a period-start format is a grain
# candidate, MySQL only (other dialects' grain IDs never shift): '%Y-%m-%d' → day,
# '%Y-%m-01' → month, '%Y-01-01' → year, each optionally followed by ' 00:00:00'; and the
# day format over a period start: `x - INTERVAL WEEKDAY(x) DAY` → week (Monday),
# `MAKEDATE(YEAR(x), 1) + INTERVAL QUARTER(x) - 1 QUARTER` → quarter (the shapes a grain
# change renders, so rendered SQL reads back with its grain). sqlglot reads
# DATE_FORMAT(x, f) as TimeToStr(TsOrDsToTimestamp(x), f) with f in Python strftime
# notation, which for these formats is the same text. Other formats ('%Y-%m', '%x-%v')
# are labels, not period starts; DATE(x) is a cast (like CAST(x AS DATE)), not a grain.
MYSQL_PERIOD_FORMATS = {"%Y-%m-%d": "day", "%Y-%m-01": "month", "%Y-01-01": "year"}
MYSQL_TIME_SUFFIX = " 00:00:00"


@dataclass(frozen=True)
class MySQLPeriod:
    """A MySQL DATE_FORMAT grain candidate: its unit, the time suffix of its format
    ('' or ' 00:00:00') and the value it truncates (a node of the tree)."""

    unit: str
    suffix: str
    value: exp.Expression


def _unit_is(node: exp.Expression, unit: str) -> bool:
    return isinstance(node, exp.Var | exp.Literal) and node.name.upper() == unit


def _mysql_week_start(node: exp.Expression) -> exp.Expression | None:
    """x of `x - INTERVAL WEEKDAY(x) DAY` or `DATE_SUB(x, INTERVAL WEEKDAY(x) DAY)` (Monday
    of x's week), else None."""
    if isinstance(node, exp.Sub) and isinstance(node.expression, exp.Interval):
        weekday, unit = node.expression.this, node.expression.args.get("unit")
    elif isinstance(node, exp.DateSub):
        weekday, unit = node.expression, node.args.get("unit")
    else:
        return None
    if not _unit_is(unit, "DAY") or not (
        isinstance(weekday, exp.Anonymous)
        and weekday.name.upper() == "WEEKDAY"
        and len(weekday.expressions) == 1
    ):
        return None
    value = node.this
    bare = value.this if isinstance(value, exp.Paren) else value
    return bare if weekday.expressions[0] == bare else None


def _mysql_quarter_start(node: exp.Expression) -> exp.Expression | None:
    """x of `MAKEDATE(YEAR(x), 1) + INTERVAL QUARTER(x) - 1 QUARTER`, else None."""
    if not isinstance(node, exp.Add) or not isinstance(node.expression, exp.Interval):
        return None
    makedate, interval = node.this, node.expression
    count = interval.this
    if not (
        isinstance(makedate, exp.Anonymous)
        and makedate.name.upper() == "MAKEDATE"
        and len(makedate.expressions) == 2
        and isinstance(makedate.expressions[0], exp.Year)
        and makedate.expressions[1] == exp.Literal.number(1)
        and _unit_is(interval.args.get("unit"), "QUARTER")
        and isinstance(count, exp.Sub)
        and isinstance(count.this, exp.Quarter)
        and count.expression == exp.Literal.number(1)
    ):
        return None
    year_arg = makedate.expressions[0].this
    value = year_arg.this if isinstance(year_arg, exp.TsOrDsToDate) else year_arg
    return value if count.this.this == value else None


def mysql_period(node: exp.Expression) -> MySQLPeriod | None:
    """The MySQL `DATE_FORMAT(x, '<period start>')` grain candidate at `node`, else None."""
    if not isinstance(node, exp.TimeToStr) or not isinstance(node.this, exp.TsOrDsToTimestamp):
        return None
    fmt = node.args.get("format")
    if not isinstance(fmt, exp.Literal) or not fmt.is_string:
        return None
    text = fmt.name
    suffix = MYSQL_TIME_SUFFIX if text.endswith(MYSQL_TIME_SUFFIX) else ""
    unit = MYSQL_PERIOD_FORMATS.get(text.removesuffix(suffix))
    if unit is None:
        return None
    formatted = node.this.this
    if unit == "day":
        week = _mysql_week_start(formatted)
        if week is not None:
            return MySQLPeriod("week", suffix, week)
        quarter = _mysql_quarter_start(formatted)
        if quarter is not None:
            return MySQLPeriod("quarter", suffix, quarter)
    if any(_aligns_to_a_period(n) for n in formatted.walk()):
        # The value is already some other period's start (e.g. a Sunday week start, or a
        # date built from YEAR(x)): another grain would truncate that, not x.
        return None
    return MySQLPeriod(unit, suffix, formatted)


# Calendar-part functions: a value computed with one is aligned to some period already.
_PERIOD_PARTS = (
    exp.Year,
    exp.Quarter,
    exp.Month,
    exp.Week,
    exp.WeekOfYear,
    exp.Day,
    exp.DayOfMonth,
    exp.DayOfWeek,
    exp.DayOfYear,
    exp.LastDay,
)
_PERIOD_PART_NAMES = {"WEEKDAY", "MAKEDATE", "YEARWEEK", "DAYOFWEEK", "DAYOFMONTH"}


def _aligns_to_a_period(node: exp.Expression) -> bool:
    if isinstance(node, _PERIOD_PARTS):
        return True
    return isinstance(node, exp.Anonymous) and node.name.upper() in _PERIOD_PART_NAMES


# ClickHouse conversions whose arguments after the first are options (scale, time zone,
# mode), never values: `toDateTime64('2026-01-01 00:00:00', 3, 'UTC')` has one value.
_CLICKHOUSE_VALUE_FIRST_FUNCS = {
    "toDate",
    "toDate32",
    "toDateTime",
    "toDateTime32",
    "toDateTime64",
    "toDecimal32",
    "toDecimal64",
    "toDecimal128",
    "toDecimal256",
    "parseDateTimeBestEffort",
    "parseDateTime64BestEffort",
    "parseDateTimeBestEffortOrNull",
    "parseDateTime64BestEffortOrNull",
}
# Result types of common ClickHouse conversions sqlglot cannot type (Anonymous).
_CLICKHOUSE_RESULT_TYPES = {
    "toDate": "Date",
    "toDate32": "Date32",
    "toDateTime": "DateTime",
    "toDateTime64": "DateTime64(3)",
    "toString": "String",
    "toStartOfWeek": "Date",
    "toMonday": "Date",
    "toStartOfMonth": "Date",
    "toStartOfQuarter": "Date",
    "toStartOfYear": "Date",
    "toStartOfDay": "DateTime",
    "toStartOfHour": "DateTime",
    "toStartOfMinute": "DateTime",
    "JSONExtractString": "String",
}


# ── Result models ───────────────────────────────────────────────────────────────────


class ColumnRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    # Base table, qualified as written (unquoted parts lower-cased); or the CTE / derived
    # table name if the column is computed there.
    table: str | None
    column: str
    type: str | None = None  # Warehouse type; None if unknown or derived.
    derived: bool = False  # True when lineage stops at a computed CTE column.


class LiteralContext(StrEnum):
    """Structural facts about where a literal sits (ADR 0015). Facts, not meaning."""

    NEGATED = "negated"  # Under a NOT (incl. NOT BETWEEN / NOT IN / NOT LIKE), or compared
    #                      with an excluding operator (<>, !=, IS DISTINCT FROM, <> ALL).
    OR_BRANCH = "or_branch"  # Inside one branch of an OR.
    CASE_CONDITION = "case_condition"  # In a CASE WHEN / IF / FILTER condition.
    JOIN_ON = "join_on"  # In an inner join's ON clause (filters rows like WHERE).
    OUTER_JOIN_ON = "outer_join_on"  # In an outer join's ON: filters attached rows only.
    HAVING = "having"  # Filters groups, after aggregation.
    SUBQUERY = "subquery"  # Inside a subquery (IN (SELECT ...), EXISTS, scalar, derived).
    VALUE_WRAPPED = "value_wrapped"  # Inside a function/arithmetic/concat on the value side.


class LiteralInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    scope: str
    value: str  # Literal text, e.g. "2026-01-01", "EMEA", "100".
    is_string: bool
    form: LiteralForm
    cast_type: str | None = None  # Source-dialect SQL type when form is TYPED.
    operator: str  # "=", ">=", "IN", "BETWEEN_LOW", "IS DISTINCT FROM", "= ANY", "ILIKE ANY",
    #               "<> ALL", "IN UNNEST", "ROW =" / "ROW IN" (element of a row value), ...
    column_sql: str | None  # The filtered expression as written, e.g. "LOWER(a.region)".
    column: ColumnRef | None
    wrapped: bool  # True if the column is inside a function/expression.
    in_list: str | None = None  # Shared by all literals of one IN (...) list.
    # ── ADR 0014/0015 (M1.1) ──
    context: list[LiteralContext] = []
    wrapper_sql: str | None = None  # Value-side wrapper, literal shown as "?", e.g.
    #                                  "STRPTIME(?, '%m-%d-%Y')", "? * 1e12", "'%' || ? || '%'".
    suggested_format: str | None = None  # strftime pattern from an adjacent format argument.
    compared_type: str | None = None  # Type of the column-side expression (the column's type
    #                                    when unwrapped; the expression's type when wrapped).


class GrainCandidate(BaseModel):
    """A date truncation that a `grain` variable could change (M1.5). Facts only."""

    model_config = ConfigDict(frozen=True)

    id: str  # "<scope>.G<n>"
    scope: str
    unit: str  # Normalized, lower case: "month", "week", "hour", "week(monday)", "isoweek";
    #            a unit read from a column is "column <name>" (e.g. "column cfg.month").
    column_sql: str  # The truncated expression as written, e.g. "cd.closed_at".
    sql: str  # The whole truncation, e.g. "DATE_TRUNC('MONTH', cd.closed_at)".
    column: ColumnRef | None = None  # Column it truncates (through CTEs), when there is one.
    column_type: str | None = None  # Type of the truncated expression, when known.
    output: str | None = None  # Output column it computes, when it is a projection's value.
    context: list[LiteralContext] = []  # Same facts as literals (or_branch, having, ...).


class RangeCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    scope: str
    column_sql: str
    start: str  # literal id
    end: str  # literal id
    shape: RangeShape


class JoinInfo(BaseModel):
    """A join found in the SQL, for mining the join map (DESIGN §3)."""

    model_config = ConfigDict(frozen=True)

    scope: str
    left_table: str  # Base table (resolved through aliases), or CTE name.
    right_table: str
    join_type: str  # inner, left, right, full, cross, semi, anti
    keys: list[tuple[str, str]]  # (left_column, right_column) equality pairs in ON.
    on_sql: str  # Full ON condition as written (may contain non-key filters).
    # Base tables behind each side, for mining the join map through CTEs: the side itself
    # when it is a base table; for a CTE / derived table, the one base table that every key
    # column of that side passes through from unchanged (renames allowed, no computation);
    # else None.
    left_base_table: str | None = None
    right_base_table: str | None = None
    base_keys: list[tuple[str, str]] = []  # `keys` as base-table columns; [] unless both
    #                                        base tables are known and there are keys.


class CteInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    reads: list[str]  # Tables and CTEs it selects from.
    outputs: list[str]


class QueryAnalysis(BaseModel):
    model_config = ConfigDict(frozen=True)

    dialect: Dialect
    ctes: list[CteInfo]
    tables: list[str]  # Base tables (not CTEs), qualified as written.
    outputs: list[str]
    literals: list[LiteralInfo]
    ranges: list[RangeCandidate]
    joins: list[JoinInfo] = []
    grains: list[GrainCandidate] = []  # Date truncations, `<scope>.G<n>` (M1.5).

    def literal(self, literal_id: str) -> LiteralInfo | None:
        return next((lit for lit in self.literals if lit.id == literal_id), None)

    def grain(self, grain_id: str) -> GrainCandidate | None:
        return next((g for g in self.grains if g.id == grain_id), None)


# ── Parsing and scopes (shared with templating) ─────────────────────────────────────


def parse(sql: str, dialect: Dialect) -> exp.Expression:
    """Parse exactly one statement or raise SqlParseError."""
    try:
        statements = [s for s in sqlglot.parse(sql, read=str(dialect)) if s is not None]
    except (ParseError, TokenError) as exc:
        raise SqlParseError(syntax_error_message(sql, dialect, exc)) from exc
    if dialect is Dialect.MYSQL:
        reject_mysql_hidden_sql(sql)
    if not statements:
        raise SqlParseError("No SQL statement found.")
    if len(statements) > 1:
        raise SqlParseError(
            f"Found {len(statements)} SQL statements separated by ';'. Use exactly one."
        )
    if dialect is Dialect.MYSQL:
        return _mysql_interval_counts(statements[0])
    return statements[0]


def _mysql_interval_counts(tree: exp.Expression) -> exp.Expression:
    """`INTERVAL WEEKDAY(ts) DAY` and `INTERVAL (WEEKDAY(ts)) DAY` are the same in MySQL,
    and sqlglot writes every computed interval count in parentheses; reading the count
    without them keeps same-query-back exact for MySQL-written SQL (ADR 0028). DATE_ADD /
    DATE_SUB keep their count in `expression` (with `unit`) instead of an Interval."""
    for node in list(tree.find_all(exp.Interval, *_MYSQL_DATE_DELTAS)):
        key = "this" if isinstance(node, exp.Interval) else "expression"
        count = node.args.get(key)
        if key == "expression" and node.args.get("unit") is None:
            continue
        if isinstance(count, exp.Paren) and not count.comments:
            node.set(key, count.this)
    return tree


_MYSQL_DATE_DELTAS = (exp.DateAdd, exp.DateSub, exp.TimestampAdd, exp.TimestampSub)


# MySQL optimizer hints that change the statement's own limits or settings: MAX_EXECUTION_TIME
# overrides the session's max_execution_time (the connector's statement timeout) and SET_VAR
# sets any session variable for the statement (ADR 0028).
_MYSQL_SETTING_HINTS = re.compile(r"\b(MAX_EXECUTION_TIME|SET_VAR)\b", re.IGNORECASE)


def reject_mysql_hidden_sql(sql: str) -> None:
    """Refuse MySQL text that the server would run differently from how cteforge reads it
    (ADR 0028). Raises SqlParseError.

    - Executable comments `/*! ... */` (and MariaDB's `/*M! ... */`): MySQL runs their
      content as SQL, but sqlglot (like every other reader) sees a comment, so a read-only
      check could not see e.g. `/*! INTO OUTFILE '...' */` or a hidden function call.
    - Optimizer hints naming MAX_EXECUTION_TIME or SET_VAR: they would lift the
      connector's statement timeout or change session settings for the statement.
    Checked on sqlglot's MySQL tokens (comments inside string literals are text)."""
    try:
        tokens = sqlglot.tokenize(sql, read=str(Dialect.MYSQL))
    except TokenError as exc:  # pragma: no cover - parse() already tokenized it
        raise SqlParseError(syntax_error_message(sql, Dialect.MYSQL, exc)) from exc
    for token in tokens:
        hint = token.token_type is TokenType.HINT
        for text in [*token.comments, *([token.text] if hint else [])]:
            if text.startswith(("!", "M!")):
                raise SqlParseError(
                    "The SQL contains a MySQL executable comment (/*! ... */). MySQL runs "
                    "the text inside it, which cteforge cannot check; write that SQL "
                    "without the comment markers, or remove it."
                )
            if (hint or text.startswith("+")) and _MYSQL_SETTING_HINTS.search(text):
                raise SqlParseError(
                    "The SQL contains a MAX_EXECUTION_TIME or SET_VAR optimizer hint. The "
                    "statement timeout and session settings come from the connection "
                    "profile; remove the hint."
                )


_QUOTE_NAMES = {"'": "quote (')", '"': 'double quote (")', "`": "backtick (`)"}


def syntax_error_message(sql: str, dialect: Dialect, exc: ParseError | TokenError) -> str:
    """A readable "SQL syntax error at line L, column C: <likely cause>" from sqlglot's error.

    Says only what sqlglot reports (position, offending token) plus the usual causes of that
    kind of failure; falls back to sqlglot's own text when it gives no position."""
    if isinstance(exc, TokenError):
        # sqlglot's TokenError position is unreliable (often the start of the SQL), so
        # find the opener that never closes ourselves.
        found = _unclosed_opener(sql, dialect)
        if found is None:
            return (
                "SQL syntax error: the SQL could not be split into words and symbols "
                f"(read as {dialect}). Check for an unclosed quote or comment."
            )
        offset, opener = found
        line = sql.count("\n", 0, offset) + 1
        col = offset - (sql.rfind("\n", 0, offset) + 1) + 1
        what = "comment (/*)" if opener == "/*" else _QUOTE_NAMES[opener]
        near = sql[offset:].split("\n", 1)[0]
        return (
            f"SQL syntax error at line {line}, column {col}: the {what} opened here "
            f"is never closed (near {_excerpt(near)})."
        )
    errors = getattr(exc, "errors", None) or []
    if not errors or not errors[0].get("line"):
        return f"SQL syntax error (read as {dialect}): {exc}"
    first = errors[0]
    token = str(first.get("highlight") or "").strip()
    col = max(1, int(first.get("col") or 1) - len(token) + 1)
    where = f"SQL syntax error at line {first['line']}, column {col}"
    description = str(first.get("description") or "").strip()
    if description.startswith("Invalid expression") or description == "Unexpected token":
        cause = (
            f"unexpected {_excerpt(token)}. Common causes: a missing comma between "
            "columns, a misspelled keyword, or an unclosed parenthesis."
        )
    elif description.startswith("Expecting )"):
        cause = f"a '(' is never closed; expected ')' before {_excerpt(token)}."
    else:
        cause = f"{description.rstrip('.')} near {_excerpt(token)}."
    return f"{where}: {cause} (Read as {dialect}; check the dialect if the SQL is valid.)"


# sqlglot's tokenizers for these read \' as an escaped quote (Redshift: like its
# QUOTE_LITERAL, which doubles backslashes; ClickHouse: STRING_ESCAPES ' and \, as its
# string-literal syntax documents; MySQL: unless the server's sql_mode has
# NO_BACKSLASH_ESCAPES, which the MySQL connector refuses — ADR 0028).
_BACKSLASH_ESCAPES = {
    Dialect.REDSHIFT,
    Dialect.SNOWFLAKE,
    Dialect.BIGQUERY,
    Dialect.DATABRICKS,
    Dialect.CLICKHOUSE,
    Dialect.MYSQL,
}


def _unclosed_opener(sql: str, dialect: Dialect) -> tuple[int, str] | None:
    """(offset, opener) of the first quote or /* comment that never closes, else None.
    A doubled quote ('') is an escaped quote; so is \\' where the dialect allows it.

    One missing closing quote shifts the pairing, so the LAST quote looks unclosed. A
    quoted span running across a line break is the likelier culprit, so when an unclosed
    quote is found, the first such earlier span's opener is reported instead."""
    i, n = 0, len(sql)
    multiline: dict[str, int] = {}  # quote char → opener offset of first multi-line span
    while i < n:
        if sql.startswith("--", i):
            end = sql.find("\n", i)
            i = n if end < 0 else end + 1
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            if end < 0:
                return i, "/*"
            i = end + 2
        elif sql[i] in _QUOTE_NAMES:
            quote, j = sql[i], i + 1
            while j < n:
                if sql[j] == "\\" and dialect in _BACKSLASH_ESCAPES:
                    j += 2
                elif sql[j] == quote and sql[j + 1 : j + 2] == quote:
                    j += 2
                elif sql[j] == quote:
                    break
                else:
                    j += 1
            if j >= n:
                return multiline.get(quote, i), quote
            if "\n" in sql[i:j]:
                multiline.setdefault(quote, i)
            i = j + 1
        else:
            i += 1
    return None


def _excerpt(text: str, limit: int = 30) -> str:
    text = " ".join(text.split())
    return f"`{text[:limit]}…`" if len(text) > limit else f"`{text}`" if text else "end of SQL"


def transpile(sql: str, source: Dialect, target: Dialect) -> str:
    """One statement written in `source`, as `target` SQL (sqlglot transpile; ADR 0013).
    Raises SqlParseError when it does not parse."""
    return generate(parse(sql, source), target)


def generate(tree: exp.Expression, dialect: Dialect, *, pretty: bool = True) -> str:
    """SQL text of a tree in `dialect`. Every SQL text cteforge writes goes through here.

    ClickHouse: sqlglot reads toStartOfDay / toStartOfMonth / ... / toMonday as a
    TimestampTrunc and would write `dateTrunc('MONTH', x)`; these are written back under
    the name the SQL used (kept in the node's meta), so trusted SQL keeps its functions
    and their return types, and date_trunc keeps its name and a lower-case unit
    (ADR 0025). MySQL: a TIMESTAMP literal is written as a literal (ADR 0028). The tree
    itself is not changed."""
    if dialect is Dialect.CLICKHOUSE:
        tree = tree.copy().transform(_clickhouse_native_name, copy=False)
    elif dialect is Dialect.MYSQL:
        tree = tree.copy().transform(_mysql_timestamp_literal, copy=False)
    return tree.sql(dialect=str(dialect), pretty=pretty)


def _mysql_timestamp_literal(node: exp.Expression) -> exp.Expression:
    """MySQL `TIMESTAMP '2026-01-01 10:00:00'` (a DATETIME literal) parses as a cast to
    TIMESTAMP, which sqlglot writes as the function `TIMESTAMP('...')` — the same value, but
    a different tree when read back. Written as the literal again (ADR 0028); the string
    itself is still escaped by sqlglot's generator."""
    if (
        isinstance(node, exp.Cast)
        and isinstance(node.this, exp.Literal)
        and node.this.is_string
        and node.to.this is exp.DataType.Type.TIMESTAMPTZ
        and not node.to.expressions
    ):
        return exp.Introducer(this=exp.Var(this="TIMESTAMP"), expression=node.this)
    return node


def _clickhouse_native_name(node: exp.Expression) -> exp.Expression:
    """toStartOf* / toMonday under their own name; date_trunc / dateTrunc under the name
    written with a lower-case unit (sqlglot writes `dateTrunc('MONTH', x)`; ClickHouse reads
    an upper-case unit only since 23.12, a lower-case one in every version)."""
    name = node.meta.get(CLICKHOUSE_NAME_KEY)
    zone = node.args.get("zone")
    tail = [zone] if zone is not None else []
    if isinstance(node, exp.TimestampTrunc) and isinstance(name, str):
        if name.startswith("toStartOf") or name == "toMonday":
            return exp.Anonymous(this=name, expressions=[node.this, *tail])
    if isinstance(node, exp.DateTrunc | exp.TimestampTrunc):
        unit = node.args.get("unit")
        if not isinstance(unit, exp.Literal | exp.Var):
            return node
        written = (
            name
            if isinstance(name, str) and name.lower() in ("datetrunc", "date_trunc")
            else "dateTrunc"
        )
        unit_text = exp.Literal.string(unit.name.lower())
        return exp.Anonymous(this=written, expressions=[unit_text, node.this, *tail])
    return node


def reject_query_parameters(tree: exp.Expression, dialect: Dialect) -> None:
    """Trusted SQL must run as written. A ClickHouse query parameter (`{name:Type}`) or a
    `?` would make ClickHouse fail ("Substitution ... is not set": cteforge never sends
    parameters) and would collide with cteforge's own template placeholders, which use the
    same syntax in ClickHouse templates (ADR 0025). Raises SqlParseError."""
    if dialect is not Dialect.CLICKHOUSE:
        return
    found = next(tree.find_all(exp.Placeholder), None)
    if found is None:
        return
    shown = generate(found, dialect, pretty=False)
    raise SqlParseError(
        f"The SQL contains the ClickHouse query parameter {shown}. Trusted SQL must run as "
        "written: replace the parameter with a literal value (e.g. 'claude-sonnet' or "
        "toDate('2026-01-01')) and mark that literal as a variable; cteforge fills it in "
        "when the definition runs."
    )


@dataclass(frozen=True)
class Scope:
    name: str
    root: exp.Expression
    is_main: bool


def _top_with(tree: exp.Expression) -> exp.With | None:
    with_ = tree.args.get("with_") or tree.args.get("with")
    return with_ if isinstance(with_, exp.With) else None


def main_scope_name(cte_names: list[str]) -> str:
    """`main`, or `main_<n>` (first free n ≥ 2) when a CTE already has that name."""
    taken = {name.lower() for name in cte_names}
    name, n = MAIN_SCOPE, 1
    while name in taken:
        n += 1
        name = f"{MAIN_SCOPE}_{n}"
    return name


def scopes(tree: exp.Expression) -> list[Scope]:
    """One scope per top-level CTE in order, then the outer query."""
    with_ = _top_with(tree)
    ctes = [Scope(c.alias_or_name, c.this, False) for c in (with_.expressions if with_ else [])]
    return [*ctes, Scope(main_scope_name([s.name for s in ctes]), tree, True)]


def _walk(scope: Scope, tree: exp.Expression) -> Iterator[exp.Expression]:
    """Depth-first, pre-order, children in argument order. The outer query skips WITH."""
    skip = _top_with(tree) if scope.is_main else None
    stack = [scope.root]
    while stack:
        node = stack.pop()
        if skip is not None and node is skip:
            continue
        yield node
        stack.extend(reversed(list(node.iter_expressions())))


def _ident_key(identifier: exp.Expression | None) -> str:
    """Lookup key for an identifier: lower-cased unless quoted."""
    if identifier is None:
        return ""
    if isinstance(identifier, exp.Identifier) and identifier.quoted:
        return identifier.name
    return identifier.name.lower()


# ── Literal location ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FoundLiteral:
    """A filter literal located in a parsed tree (internal; not serialized)."""

    id: str
    scope: str
    node: exp.Expression  # The node to replace: Cast for typed, Neg for negative, else Literal.
    literal: exp.Literal | exp.Boolean
    predicate: exp.Expression  # The comparison / IN / BETWEEN / LIKE it belongs to.
    operator: str
    other_side: exp.Expression | None  # The filtered (column-side) expression.
    value_root: exp.Expression  # Predicate operand holding the literal (`node` if unwrapped).
    context: tuple[LiteralContext, ...]
    format_literal: exp.Literal | None  # Format argument of a date-parsing function around it.

    @property
    def value_wrapped(self) -> bool:
        return LiteralContext.VALUE_WRAPPED in self.context


@dataclass(frozen=True)
class _Located:
    node: exp.Expression
    value_root: exp.Expression
    predicate: exp.Expression
    operator: str
    other: exp.Expression


def _target(literal: exp.Expression) -> exp.Expression:
    parent = literal.parent
    if isinstance(parent, exp.Cast) and parent.this is literal:
        return parent
    if isinstance(parent, exp.Neg):
        return parent
    return literal


def _is_wrapper(node: exp.Expression) -> bool:
    return isinstance(node, _WRAPPERS) and not isinstance(node, _NOT_WRAPPERS)


def _has_reference(node: exp.Expression, dialect: Dialect | None = None) -> bool:
    if node.find(*_REFERENCES) is not None:
        return True
    # ClickHouse lambdas: `x -> x = 'web_search'` compares the array's elements.
    return dialect is Dialect.CLICKHOUSE and any(
        lambda_binding(i) is not None for i in node.find_all(exp.Identifier)
    )


def lambda_binding(ident: exp.Expression) -> exp.Expression | None:
    """The array a lambda parameter ranges over: for `x` in `arrayExists(x -> x = 'a', tools)`
    the `tools` argument (parameter i ↔ the i-th non-lambda argument). None otherwise."""
    if not isinstance(ident, exp.Identifier) or isinstance(ident.parent, exp.Column):
        return None
    child, node = ident, ident.parent
    while node is not None:
        if isinstance(node, exp.Lambda):
            if child.arg_key == "expressions":  # the parameter's own declaration
                return None
            params = [p.name for p in node.expressions]
            if ident.name in params:
                func = node.parent
                arrays = [a for a in func.iter_expressions() if a is not node] if func else []
                index = params.index(ident.name)
                return arrays[index] if index < len(arrays) else None
        child, node = node, node.parent
    return None


def array_join_binding(column: exp.Expression) -> exp.Expression | None:
    """The array an unqualified column ranges over through ClickHouse ARRAY JOIN:
    `ARRAY JOIN tool_names AS tool` binds `tool`, `ARRAY JOIN tool_names` rebinds
    `tool_names` to its elements. None otherwise."""
    if not isinstance(column, exp.Column) or column.table:
        return None
    select = column.find_ancestor(exp.Select)
    while select is not None:
        for join in select.args.get("joins") or []:
            if (join.kind or "").upper() != "ARRAY":
                continue
            items = [join.this, *(join.expressions or [])]
            for item in items:
                if isinstance(item, exp.Alias) and item.alias == column.name:
                    return item.this
                if isinstance(item, exp.Column) and item.name == column.name and item is not column:
                    return item
        select = select.find_ancestor(exp.Select)
    return None


def _is_unit_arg(parent: exp.Expression, node: exp.Expression) -> bool:
    """`'month'` in an unparsed `DATE_PART('month', ...)`: a date-part unit, not a value."""
    return (
        isinstance(parent, exp.Anonymous)
        and parent.name.upper() in _UNIT_FIRST_FUNCS
        and node.arg_key == "expressions"
        and node.index == 0
    )


def _is_option_arg(parent: exp.Expression, node: exp.Expression, dialect: Dialect | None) -> bool:
    """ClickHouse `toDateTime64(value, 3, 'UTC')`: the scale / time zone after the value."""
    return (
        dialect is Dialect.CLICKHOUSE
        and isinstance(parent, exp.Anonymous)
        and parent.name in _CLICKHOUSE_VALUE_FIRST_FUNCS
        and node.arg_key == "expressions"
        and (node.index or 0) > 0
    )


def _climbable(
    parent: exp.Expression, node: exp.Expression, dialect: Dialect | None = None
) -> bool:
    if node.arg_key in _NON_VALUE_ARGS or _is_unit_arg(parent, node):
        return False
    if _is_option_arg(parent, node, dialect):
        return False
    return _is_wrapper(parent) or isinstance(parent, exp.Tuple | exp.Any | exp.All)


def _is_predicate(node: exp.Expression | None, dialect: Dialect | None) -> bool:
    if isinstance(node, _PREDICATES):
        return True
    if dialect is not Dialect.CLICKHOUSE:
        return False
    if isinstance(node, exp.ArrayContains | exp.RegexpLike):
        return True
    return isinstance(node, exp.Anonymous) and node.name in _CLICKHOUSE_LIST_FUNCS


def _locate(literal: exp.Expression, dialect: Dialect | None = None) -> _Located | None:
    """Climb from a literal through value-side wrappers to its predicate, if any."""
    target = node = _target(literal)
    path = [node]
    while not _is_predicate(node.parent, dialect):
        parent = node.parent
        if parent is None or not _climbable(parent, node, dialect):
            return None
        node = parent
        path.append(node)
    predicate = node.parent
    operand = _classify(predicate, path[::-1], dialect)
    if operand is None:
        return None
    return _Located(target, operand.value_root, predicate, operand.operator, operand.other)


@dataclass(frozen=True)
class _Operand:
    operator: str
    value_root: exp.Expression  # Value-side expression holding the literal (a list element).
    other: exp.Expression | None  # The column side it is compared to.


def _quantifier(node: exp.Expression) -> str | None:
    if isinstance(node, exp.Any):
        return "ANY"
    if isinstance(node, exp.All):
        return "ALL"
    if isinstance(node, exp.Anonymous) and node.name.upper() in _QUANTIFIER_FUNCS:
        return node.name.upper()
    return None


def _list_element(nodes: list[exp.Expression], i: int) -> exp.Expression:
    """The list element on the path below nodes[i] (through parentheses and one ARRAY /
    tuple); nodes[i] itself when there is no list."""
    j = i
    while j + 1 < len(nodes) and isinstance(nodes[j], exp.Paren):
        j += 1
    if j + 1 < len(nodes) and isinstance(nodes[j], exp.Array | exp.Tuple):
        return nodes[j + 1]
    return nodes[i]


def _row_element(
    operator: str, nodes: list[exp.Expression], other: exp.Expression
) -> _Operand | None:
    """Element of a row value `(1, 2)`, compared to the matching element of `(a, b)`."""
    row, element = nodes[0], nodes[1]
    if not isinstance(other, exp.Tuple) or len(other.expressions) != len(row.expressions):
        return None
    return _Operand(f"ROW {operator}", element, other.expressions[element.index])


def _in_operand(predicate: exp.In, nodes: list[exp.Expression]) -> _Operand | None:
    head = nodes[0]
    if head.arg_key == "unnest" and len(nodes) > 1:  # x IN UNNEST([...])
        return _Operand("IN UNNEST", _list_element(nodes, 1), predicate.this)
    if not any(e is head for e in predicate.expressions):
        return None
    if isinstance(head, exp.Tuple) and len(nodes) > 1:  # (a, b) IN ((1, 2), ...)
        return _row_element("IN", nodes, predicate.this)
    return _Operand("IN", head, predicate.this)


def _comparison_operand(predicate: exp.Expression, nodes: list[exp.Expression]) -> _Operand:
    head = nodes[0]
    operator = _COMPARISONS[type(predicate)]
    other = predicate.left if head is predicate.right else predicate.right
    if head is predicate.left:  # literal on the left: 5 < x  →  x > 5
        operator = _FLIP.get(operator, operator)
    quantifier = _quantifier(head)
    if quantifier is not None and len(nodes) > 1:  # x = ANY(ARRAY[...]), d ILIKE ANY (...)
        return _Operand(f"{operator} {quantifier}", _list_element(nodes, 1), other)
    if isinstance(head, exp.Tuple) and len(nodes) > 1:  # (a, b) = (1, 2)
        return _row_element(operator, nodes, other) or _Operand(operator, head, other)
    return _Operand(operator, head, other)


def _clickhouse_operand(predicate: exp.Expression, nodes: list[exp.Expression]) -> _Operand | None:
    """`has(arr, 'x')`, `match(s, 're')`: the literal is the second argument;
    `hasAny(arr, ['a', 'b'])` / `hasAll`: each element of the literal array."""
    head = nodes[0]
    if isinstance(predicate, exp.ArrayContains | exp.RegexpLike):
        if head.arg_key != "expression":
            return None
        operator = "HAS" if isinstance(predicate, exp.ArrayContains) else "MATCH"
        return _Operand(operator, head, predicate.this)
    args = predicate.expressions
    if len(args) != 2 or head is not args[1] or not isinstance(head, exp.Array) or len(nodes) < 2:
        return None
    return _Operand(_CLICKHOUSE_LIST_FUNCS[predicate.name], _list_element(nodes, 0), args[0])


def _classify(
    predicate: exp.Expression, nodes: list[exp.Expression], dialect: Dialect | None = None
) -> _Operand | None:
    """The literal's operand when `nodes` (predicate operand first, literal last) is the
    value side of `predicate`."""
    if isinstance(predicate, exp.In):
        operand = _in_operand(predicate, nodes)
    elif isinstance(predicate, exp.Between):
        head = nodes[0]
        if head.arg_key not in ("low", "high"):
            return None
        operand = _Operand(f"BETWEEN_{head.arg_key.upper()}", head, predicate.this)
    elif isinstance(predicate, tuple(_COMPARISONS)):
        operand = _comparison_operand(predicate, nodes)
    else:
        operand = _clickhouse_operand(predicate, nodes)
    if operand is None or _has_reference(operand.value_root, dialect):
        return None
    if operand.other is None or not _has_reference(operand.other, dialect):
        return None
    return operand


def _join_on_facts(join: exp.Join) -> tuple[LiteralContext, ...]:
    """Outer joins filter attached rows; semi joins filter like inner joins; anti joins
    keep the rows that do NOT match (like NOT EXISTS)."""
    join_type = _join_type(join, has_condition=True)
    if join_type in ("left", "right", "full"):
        return (LiteralContext.OUTER_JOIN_ON,)
    if join_type == "anti":
        return (LiteralContext.NEGATED, LiteralContext.JOIN_ON)
    return (LiteralContext.JOIN_ON,)


def _ancestor_facts(parent: exp.Expression, child: exp.Expression) -> tuple[LiteralContext, ...]:
    if isinstance(parent, exp.Not):
        return (LiteralContext.NEGATED,)
    if isinstance(parent, exp.Or):
        return (LiteralContext.OR_BRANCH,)
    if isinstance(parent, exp.If) and child.arg_key == "this":
        return (LiteralContext.CASE_CONDITION,)
    if isinstance(parent, exp.Filter) and child.arg_key == "expression":
        return (LiteralContext.CASE_CONDITION,)
    if isinstance(parent, exp.Having):
        return (LiteralContext.HAVING,)
    if isinstance(parent, exp.Join) and child.arg_key == "on":
        return _join_on_facts(parent)
    if isinstance(parent, exp.Subquery | exp.Exists | exp.CTE):
        return (LiteralContext.SUBQUERY,)
    return ()


def _context(located: _Located, scope_root: exp.Expression) -> tuple[LiteralContext, ...]:
    facts: set[LiteralContext] = set()
    node = located.node
    while node is not located.value_root:
        node = node.parent
        if not isinstance(node, exp.Paren):
            facts.add(LiteralContext.VALUE_WRAPPED)
    if located.predicate.args.get("negate"):  # e.g. DuckDB `x NOT LIKE 'a'`
        facts.add(LiteralContext.NEGATED)
    if _excluding(located.operator):  # `x <> 'void'` keeps everything BUT the value
        facts.add(LiteralContext.NEGATED)
    child = located.predicate
    while child is not scope_root and child.parent is not None:
        facts.update(_ancestor_facts(child.parent, child))
        child = child.parent
    return tuple(c for c in LiteralContext if c in facts)


def _format_literal(located: _Located) -> exp.Literal | None:
    node = located.node
    while node is not located.value_root:
        parent = node.parent
        if isinstance(parent, _PARSE_FUNCS) and node.arg_key == "this":
            fmt = parent.args.get("format")
            if isinstance(fmt, exp.Literal) and fmt.is_string:
                return fmt
        node = parent
    return None


def find_literals(tree: exp.Expression, dialect: Dialect | None = None) -> list[FoundLiteral]:
    """All filter literals in the tree with stable IDs (see module docstring). `dialect`
    enables dialect-only shapes (ClickHouse has / hasAny / match / lambdas)."""
    found: list[FoundLiteral] = []
    for scope in scopes(tree):
        ordinal = 0
        for node in _walk(scope, tree):
            if not isinstance(node, exp.Literal | exp.Boolean):
                continue
            located = _locate(node, dialect)
            if located is None:
                continue
            ordinal += 1
            found.append(
                FoundLiteral(
                    id=f"{scope.name}.L{ordinal}",
                    scope=scope.name,
                    node=located.node,
                    literal=node,
                    predicate=located.predicate,
                    operator=located.operator,
                    other_side=located.other,
                    value_root=located.value_root,
                    context=_context(located, scope.root),
                    format_literal=_format_literal(located),
                )
            )
    return found


# ── Grain candidates (M1.5) ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FoundGrain:
    """A date truncation located in a parsed tree (internal; not serialized)."""

    id: str
    scope: str
    node: exp.Expression  # DateTrunc / TimestampTrunc / DatetimeTrunc (or `is_truncation`).
    unit: str  # Normalized lower-case unit (see `truncation_unit`).
    context: tuple[LiteralContext, ...]


def is_truncation(node: exp.Expression, dialect: Dialect) -> bool:
    """A grain candidate node: DateTrunc / TimestampTrunc / DatetimeTrunc, in ClickHouse
    also toStartOfWeek(...) and toStartOfInterval(...) (Anonymous in sqlglot), in MySQL
    DATE_FORMAT(x, '<period start>') (`mysql_period`)."""
    if isinstance(node, TRUNCATIONS):
        return True
    if dialect is Dialect.MYSQL:
        return mysql_period(node) is not None
    return (
        dialect is Dialect.CLICKHOUSE
        and isinstance(node, exp.Anonymous)
        and node.name in (CLICKHOUSE_WEEK_FUNC, CLICKHOUSE_INTERVAL_FUNC)
        and bool(node.expressions)
    )


def truncated_expression(node: exp.Expression) -> exp.Expression:
    """The expression a truncation node truncates (first argument of a ClickHouse call;
    the value whose period start a MySQL DATE_FORMAT formats)."""
    if isinstance(node, exp.TimeToStr):
        period = mysql_period(node)
        if period is not None:
            return period.value
    return node.expressions[0] if isinstance(node, exp.Anonymous) else node.this


def _clickhouse_week_unit(node: exp.Anonymous, dialect: Dialect) -> str:
    """toStartOfWeek(x[, mode[, tz]]): the mode picks the first day as in toWeek: even
    modes (and the default, 0) start on Sunday, odd modes on Monday. Monday is ClickHouse's
    native week (toMonday, date_trunc('week'), toStartOfInterval WEEK), so `week`;
    Sunday is `week(sunday)` (not a changeable grain); a non-literal mode is reported."""
    mode = node.expressions[1] if len(node.expressions) > 1 else None
    if mode is None:
        return "week(sunday)"
    if isinstance(mode, exp.Literal) and not mode.is_string and mode.name.isdigit():
        return "week" if int(mode.name) % 2 else "week(sunday)"
    return f"week(mode {mode.sql(dialect=str(dialect))})"


def _clickhouse_interval_unit(node: exp.Anonymous, dialect: Dialect) -> str:
    """toStartOfInterval(x, INTERVAL n unit[, origin][, tz]): `unit` when n = 1 and no
    origin; else a descriptive unit that no grain value equals."""
    args = node.expressions
    interval = args[1] if len(args) > 1 else None
    if not isinstance(interval, exp.Interval) or interval.args.get("unit") is None:
        return node.sql(dialect=str(dialect)).lower()
    unit = _unit_name(interval.args["unit"].name)
    count = interval.this
    zone = len(args) == 3 and isinstance(args[2], exp.Literal) and args[2].is_string
    origin = len(args) > 3 or (len(args) == 3 and not zone)
    if isinstance(count, exp.Literal) and count.name == "1" and not origin:
        return unit
    shown = count.sql(dialect=str(dialect)) if count is not None else "?"
    return f"{shown} {unit}" + (" with origin" if origin else "")


def truncation_unit(node: exp.Expression, dialect: Dialect) -> str:
    """Normalized lower-case unit of a truncation node. Abbreviations are spelled out
    ("MM" → "month", "Q" → "quarter"). BigQuery parses `WEEK` as WEEK(SUNDAY), which is its
    native week, so that is "week"; any other explicit week start is "week(<day>)". A
    qualified column is "column <name>"; a bare column name needs the SQL's tokens
    (`identifier_units`), because sqlglot parses it as the unit keyword. ClickHouse: see
    `_clickhouse_week_unit` / `_clickhouse_interval_unit`. MySQL DATE_FORMAT: the unit of
    its period start (`mysql_period`)."""
    if isinstance(node, exp.TimeToStr):
        period = mysql_period(node)
        return period.unit if period is not None else ""
    if isinstance(node, exp.Anonymous):
        if node.name == CLICKHOUSE_WEEK_FUNC:
            return _clickhouse_week_unit(node, dialect)
        return _clickhouse_interval_unit(node, dialect)
    unit = node.args.get("unit")
    if isinstance(unit, exp.WeekStart):
        day = unit.this.name.lower() if unit.this is not None else ""
        native = dialect is Dialect.BIGQUERY and day == "sunday"
        return "week" if native else f"week({day})"
    if isinstance(unit, exp.Column) and len(unit.parts) != 1:
        # A qualified column (`cfg.month`): its value picks the unit at run time.
        return COLUMN_UNIT_PREFIX + unit.sql(dialect=str(dialect))
    if isinstance(unit, exp.Var | exp.Literal | exp.Column):
        return _unit_name(unit.name)
    return unit.sql(dialect=str(dialect)).lower() if unit is not None else ""


def _unit_name(text: str) -> str:
    name = text.upper()
    name = exp.TimeUnit.UNABBREVIATED_UNIT_NAME.get(name, _UNIT_ALIASES.get(name, name))
    return name.lower()


def _call_args(tokens: list[Token], start: int) -> list[list[Token]]:
    """Top-level arguments of the call whose `(` is tokens[start - 1], as token runs."""
    args: list[list[Token]] = [[]]
    depth = 0
    for token in tokens[start:]:
        if token.token_type in (TokenType.L_PAREN, TokenType.L_BRACKET):
            depth += 1
        elif token.token_type in (TokenType.R_PAREN, TokenType.R_BRACKET):
            if depth == 0:
                break
            depth -= 1
        elif token.token_type is TokenType.COMMA and depth == 0:
            args.append([])
            continue
        args[-1].append(token)
    return args


def identifier_units(sql: str, dialect: Dialect) -> dict[str, str]:
    """Truncation units written as a bare (or double-quoted) name in a dialect where that
    name is a column (DuckDB, Postgres, Databricks): normalized unit → name as written.
    sqlglot parses `DATE_TRUNC(month, ts)` there like `DATE_TRUNC('month', ts)`, so only
    the tokens tell them apart. A name used both ways in one query counts as a column."""
    positions = _STRING_UNIT_ARGS.get(dialect)
    if not positions:
        return {}
    tokens = list(sqlglot.tokenize(sql, read=str(dialect)))
    names: dict[str, str] = {}
    for i, token in enumerate(tokens[:-1]):
        index = positions.get(token.text.upper())
        if index is None or tokens[i + 1].token_type is not TokenType.L_PAREN:
            continue
        args = _call_args(tokens, i + 2)
        if len(args) <= index or len(args[index]) != 1:
            continue  # Missing, or a longer expression (sqlglot keeps those as written).
        unit = args[index][0]
        if unit.token_type not in (TokenType.STRING, TokenType.NATIONAL_STRING):
            names.setdefault(_unit_name(unit.text), unit.text)
    return names


def _grain_context(node: exp.Expression, scope_root: exp.Expression) -> tuple[LiteralContext, ...]:
    facts: set[LiteralContext] = set()
    child = node
    while child is not scope_root and child.parent is not None:
        facts.update(_ancestor_facts(child.parent, child))
        child = child.parent
    return tuple(c for c in LiteralContext if c in facts)


def find_grains(tree: exp.Expression, dialect: Dialect) -> list[FoundGrain]:
    """Every date truncation node with its stable `<scope>.G<n>` ID (module docstring)."""
    found: list[FoundGrain] = []
    for scope in scopes(tree):
        ordinal = 0
        for node in _walk(scope, tree):
            if not is_truncation(node, dialect):
                continue
            ordinal += 1
            found.append(
                FoundGrain(
                    id=f"{scope.name}.G{ordinal}",
                    scope=scope.name,
                    node=node,
                    unit=truncation_unit(node, dialect),
                    context=_grain_context(node, scope.root),
                )
            )
    return found


def wrapper_sql(found: FoundLiteral, dialect: Dialect) -> str | None:
    """The value-side wrapper with this literal shown as `?`; None when not wrapped."""
    if not found.value_wrapped:
        return None
    path: list[tuple[str, int | None]] = []
    node = found.node
    while node is not found.value_root:
        path.append((node.arg_key, node.index))
        node = node.parent
    copy = found.value_root.copy()
    target: exp.Expression = copy
    for key, index in reversed(path):
        child = target.args[key]
        target = child[index] if isinstance(child, list) else child
    target.replace(exp.Var(this="?"))
    return generate(copy, dialect, pretty=False)


# ── Schema lookups ──────────────────────────────────────────────────────────────────

_Parts = tuple[tuple[str, bool], ...]  # (identifier, quoted) per qualifier level


def _ident_match(key: str, name: str, quoted: bool) -> bool:
    return key == name if quoted else key.lower() == name.lower()


def _parts_match(key: tuple[str, ...], parts: _Parts) -> bool:
    return all(_ident_match(k, n, q) for k, (n, q) in zip(key, parts, strict=True))


class SchemaIndex:
    """Finds a table's columns. Unquoted identifiers match case-insensitively, quoted ones
    exactly. The fully qualified key wins; otherwise the single key whose overlapping
    trailing parts match (e.g. SQL `analytics.deals` → key `deals`, if unambiguous)."""

    def __init__(self, schema: Schema | None) -> None:
        self._entries = [
            (tuple(p.strip('"`') for p in key.split(".")), columns)
            for key, columns in (schema or {}).items()
        ]

    def columns(self, parts: _Parts) -> dict[str, str] | None:
        exact = [c for k, c in self._entries if len(k) == len(parts) and _parts_match(k, parts)]
        if exact:
            return exact[0]
        loose = [
            c
            for k, c in self._entries
            if _parts_match(k[-min(len(k), len(parts)) :], parts[-min(len(k), len(parts)) :])
        ]
        return loose[0] if len(loose) == 1 else None

    def column_type(self, parts: _Parts, column: exp.Expression) -> str | None:
        columns = self.columns(parts)
        if columns is None:
            return None
        quoted = isinstance(column, exp.Identifier) and column.quoted
        return next((t for c, t in columns.items() if _ident_match(c, column.name, quoted)), None)


def _table_parts(table: exp.Table) -> _Parts:
    if not isinstance(table.this, exp.Identifier):
        return ()
    return tuple(
        (p.name, bool(p.args.get("quoted"))) for p in table.parts if isinstance(p, exp.Identifier)
    )


def _display(parts: _Parts) -> str:
    return ".".join(name if quoted else name.lower() for name, quoted in parts)


# ── Column lineage ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Lineage:
    ctes: dict[str, tuple[str, exp.Expression]]  # lookup key → (name as written, body)
    schema: SchemaIndex
    dialect: Dialect
    with_: exp.With | None = None  # The top-level WITH the CTEs come from.
    positions: dict[str, int] = field(default_factory=dict)  # lookup key → index in WITH
    # Memo of column-side expression types: id(node) → (node, type). One analysis only.
    types: dict[int, tuple[exp.Expression, exp.DataType | None]] = field(default_factory=dict)


@dataclass(frozen=True)
class _Source:
    name: str  # Qualified base table, CTE name, or derived-table alias.
    parts: _Parts | None  # Base table only.
    query: exp.Expression | None  # CTE body or derived-table query.


@dataclass(frozen=True)
class _Resolved:
    ref: ColumnRef
    expr: exp.Expression | None = None  # The computing expression when derived.


def _cte_map(tree: exp.Expression) -> dict[str, tuple[str, exp.Expression]]:
    with_ = _top_with(tree)
    return {
        _ident_key(cte.args["alias"].this): (cte.alias_or_name, cte.this)
        for cte in (with_.expressions if with_ else [])
    }


def _cte_positions(tree: exp.Expression) -> dict[str, int]:
    with_ = _top_with(tree)
    ctes = with_.expressions if with_ else []
    return {_ident_key(cte.args["alias"].this): i for i, cte in enumerate(ctes)}


def _defining_cte(node: exp.Expression, with_: exp.With) -> int | None:
    """Index of the top-level CTE whose body holds `node`; None in the outer query."""
    child = node
    while child.parent is not None:
        if child.parent is with_:
            return child.index
        child = child.parent
    return None


def _cte_visible(node: exp.Expression, key: str, lin: _Lineage) -> bool:
    """A CTE name is visible in the outer query and in CTEs defined after it; a CTE's own
    body and earlier CTEs read the base table of that name. WITH RECURSIVE sees all."""
    if key not in lin.positions or lin.with_ is None:
        return False
    if lin.with_.args.get("recursive"):
        return True
    defining = _defining_cte(node, lin.with_)
    return defining is None or lin.positions[key] < defining


def _source_of(node: exp.Expression, lin: _Lineage) -> _Source | None:
    if isinstance(node, exp.Table):
        parts = _table_parts(node)
        if not parts:
            return None
        if len(parts) == 1 and _cte_visible(node, _ident_key(node.this), lin):
            name, body = lin.ctes[_ident_key(node.this)]
            return _Source(name, None, body)
        return _Source(_display(parts), parts, None)
    if isinstance(node, exp.Subquery):
        return _Source(node.alias_or_name, None, node.this)
    return None


def _alias_key(node: exp.Expression) -> str:
    alias = node.args.get("alias")
    return _ident_key(alias.this if alias is not None else node.this)


def _sources(select: exp.Expression, lin: _Lineage) -> dict[str, _Source]:
    """alias key → source, for the FROM and JOINs of this SELECT."""
    from_ = select.args.get("from_")
    nodes = [from_.this] if from_ is not None else []
    nodes += [j.this for j in select.args.get("joins") or []]
    result: dict[str, _Source] = {}
    for node in nodes:
        source = _source_of(node, lin)
        if source is not None:
            result[_alias_key(node)] = source
    return result


def _has_column(source: _Source, column: exp.Expression, lin: _Lineage) -> bool:
    if source.parts is not None:
        return lin.schema.column_type(source.parts, column) is not None
    names = source.query.named_selects if source.query is not None else []
    return column.name.lower() in {n.lower() for n in names}


def _pick_source(column: exp.Column, sources: dict[str, _Source], lin: _Lineage) -> _Source | None:
    if column.table:
        return sources.get(_ident_key(column.args["table"]))
    if len(sources) == 1:
        return next(iter(sources.values()))
    return next((s for s in sources.values() if _has_column(s, column.this, lin)), None)


def _resolve(column: exp.Column, lin: _Lineage, depth: int = 0) -> _Resolved:
    """Trace a column to its base table (through CTEs and derived tables). A ClickHouse
    ARRAY JOIN name resolves to the array column it ranges over."""
    if lin.dialect is Dialect.CLICKHOUSE and depth <= _MAX_DEPTH:
        bound = array_join_binding(column)
        if bound is not None:
            inner = bound if isinstance(bound, exp.Column) else bound.find(exp.Column)
            if inner is not None:
                return _resolve(inner, lin, depth + 1)
    select = column.find_ancestor(exp.Select)
    source = None
    while select is not None and source is None:
        source = _pick_source(column, _sources(select, lin), lin)
        if not column.table:
            break
        select = select.find_ancestor(exp.Select)  # correlated reference to an outer query
    if source is None:
        return _Resolved(ColumnRef(table=None, column=_ident_key(column.this)))
    return _resolve_in(source, column.this, lin, depth)


def _resolve_in(source: _Source, ident: exp.Expression, lin: _Lineage, depth: int) -> _Resolved:
    name = _ident_key(ident)
    if source.parts is not None:
        col_type = lin.schema.column_type(source.parts, ident)
        return _Resolved(ColumnRef(table=source.name, column=name, type=col_type))
    derived = _Resolved(ColumnRef(table=source.name, column=name, derived=True))
    query = source.query
    if depth > _MAX_DEPTH or not isinstance(query, exp.Select):
        return derived
    for projection in query.selects:
        if isinstance(projection, exp.Star):
            inner = list(_sources(query, lin).values())
            if len(inner) == 1:
                return _resolve_in(inner[0], ident, lin, depth + 1)
        elif projection.alias_or_name.lower() == ident.name.lower():
            computed = projection.unalias()
            if isinstance(computed, exp.Column):
                return _resolve(computed, lin, depth + 1)
            return _Resolved(derived.ref, computed)
    return derived


# ── Compared type ───────────────────────────────────────────────────────────────────


def _annotate(expr: exp.Expression, dialect: Dialect) -> exp.DataType | None:
    """sqlglot type annotation, best effort (it has no complete function catalog)."""
    try:
        return annotate_types(expr, dialect=str(dialect)).type
    except (SqlglotError, ValueError, KeyError, TypeError, AttributeError):
        return None


def _expr_type(expr: exp.Expression, lin: _Lineage, depth: int = 0) -> exp.DataType | None:
    """Type of a column-side expression: annotated from its columns' warehouse types.
    Memoized per node, so shared lineage (`x + x + x` over CTE chains) is typed once."""
    cached = lin.types.get(id(expr))
    if cached is not None and cached[0] is expr:
        return cached[1]
    if depth > _MAX_DEPTH:
        return None
    result = _compute_type(expr, lin, depth)
    lin.types[id(expr)] = (expr, result)
    return result


def _bound_array(expr: exp.Expression, lin: _Lineage) -> exp.Expression | None:
    """ClickHouse: the array a lambda parameter or an ARRAY JOIN name ranges over."""
    if lin.dialect is not Dialect.CLICKHOUSE:
        return None
    if isinstance(expr, exp.Identifier):
        return lambda_binding(expr)
    return array_join_binding(expr)


def _clickhouse_type(expr: exp.Expression, lin: _Lineage, depth: int) -> exp.DataType | None:
    """Types sqlglot cannot infer for ClickHouse: elements of a lambda parameter / ARRAY
    JOIN name / `arr[i]` / `map['k']`, and common conversion functions."""
    bound = _bound_array(expr, lin)
    if bound is not None:
        array_type = _expr_type(bound, lin, depth + 1)
        return element_type(array_type) if array_type is not None else None
    if isinstance(expr, exp.Bracket):
        container = _expr_type(expr.this, lin, depth + 1)
        return element_type(container) if container is not None else None
    name = expr.meta.get(CLICKHOUSE_NAME_KEY) if isinstance(expr, exp.Func) else None
    if isinstance(expr, exp.Anonymous):
        name = expr.name
        if name == CLICKHOUSE_INTERVAL_FUNC and expr.expressions:
            return _expr_type(expr.expressions[0], lin, depth + 1)
    known = _CLICKHOUSE_RESULT_TYPES.get(name) if isinstance(name, str) else None
    return data_type(known, lin.dialect) if known is not None else None


def _compute_type(expr: exp.Expression, lin: _Lineage, depth: int) -> exp.DataType | None:
    if lin.dialect is Dialect.CLICKHOUSE:
        clickhouse = _clickhouse_type(expr, lin, depth)
        if clickhouse is not None:
            return clickhouse
    if isinstance(expr, exp.Column):
        resolved = _resolve(expr, lin)
        if resolved.ref.type:
            return data_type(resolved.ref.type, lin.dialect)
        return _expr_type(resolved.expr, lin, depth + 1) if resolved.expr is not None else None
    # annotate_types re-resolves bare columns (and forgets types set on them), so each
    # typed column is swapped for CAST(NULL AS <type>), which it types reliably.
    copy = expr.copy()
    for original, clone in zip(
        list(expr.find_all(exp.Column)), list(copy.find_all(exp.Column)), strict=True
    ):
        column_type = _expr_type(original, lin, depth + 1)
        if column_type is not None:
            clone.replace(exp.Cast(this=exp.Null(), to=column_type.copy()))
    if lin.dialect is Dialect.CLICKHOUSE:  # lambda parameters (`lower(x) = 'a'`)
        params = [i for i in expr.find_all(exp.Identifier) if lambda_binding(i) is not None]
        for param in params:
            param_type = _expr_type(param, lin, depth + 1)
            clone = _same_position(expr, param, copy)
            if param_type is not None and clone is not None:
                clone.replace(exp.Cast(this=exp.Null(), to=param_type.copy()))
    annotated = _annotate(copy, lin.dialect)
    if annotated is not None and annotated.this != exp.DataType.Type.UNKNOWN:
        return annotated
    return _preserved_type(expr, lin, depth)


def _same_position(
    root: exp.Expression, node: exp.Expression, copy: exp.Expression
) -> exp.Expression | None:
    """The node in `copy` (a copy of `root`) at the position `node` has in `root`."""
    path: list[tuple[str, int | None]] = []
    while node is not root:
        if node.parent is None:
            return None
        path.append((node.arg_key, node.index))
        node = node.parent
    target: exp.Expression = copy
    for key, index in reversed(path):
        child = target.args.get(key)
        target = child[index] if isinstance(child, list) and index is not None else child
        if not isinstance(target, exp.Expression):
            return None
    return target


def _preserved_type(expr: exp.Expression, lin: _Lineage, depth: int) -> exp.DataType | None:
    """The column's type when every wrapper between it and `expr` keeps the type."""
    column = expr.find(exp.Column)
    base = _expr_type(column, lin, depth + 1) if column is not None else None
    if column is None or base is None:
        return None
    node = column.parent
    while node is not None:
        keeps = isinstance(node, _TYPE_PRESERVING) or (
            isinstance(node, _ARITHMETIC) and base.this in exp.DataType.NUMERIC_TYPES
        )
        if not keeps:
            return None
        if node is expr:
            return base
        node = node.parent
    return None


def _compared_type(
    other: exp.Expression,
    resolved: _Resolved | None,
    lin: _Lineage,
    element: bool = False,
) -> str | None:
    """Type of the column side. `element`: the element type of that (array) side, what
    ClickHouse `has` / `hasAny` / `hasAll` compare the literal with."""
    if (
        not element
        and isinstance(other, exp.Column)
        and resolved is not None
        and resolved.ref.type
        and _bound_array(other, lin) is None
    ):
        return resolved.ref.type
    dtype = _expr_type(other, lin)
    if element and dtype is not None:
        dtype = element_type(dtype)
    return dtype.sql(dialect=str(lin.dialect)) if dtype is not None else None


# ── Literal infos ───────────────────────────────────────────────────────────────────


def _literal_value(found: FoundLiteral) -> str:
    lit = found.literal
    value = lit.name if isinstance(lit, exp.Literal) else ("TRUE" if lit.this else "FALSE")
    return f"-{value}" if isinstance(found.node, exp.Neg) else value


def _suggested_format(found: FoundLiteral, value: str) -> str | None:
    """sqlglot stores format arguments as Python strftime patterns; keep one only if it
    actually reads the original value."""
    if found.format_literal is None:
        return None
    pattern = found.format_literal.name
    try:
        datetime.strptime(value, pattern)
    except ValueError:
        return None
    return pattern


def _column_node(other: exp.Expression | None, lin: _Lineage) -> exp.Expression | None:
    """The column a column-side expression filters: itself, its first column, or (ClickHouse
    lambda) the column of the array its parameter ranges over."""
    if other is None:
        return None
    if isinstance(other, exp.Column):
        return other
    column = other.find(exp.Column)
    if column is None and lin.dialect is Dialect.CLICKHOUSE:
        for ident in other.find_all(exp.Identifier):
            bound = lambda_binding(ident)
            if bound is not None:
                return bound if isinstance(bound, exp.Column) else bound.find(exp.Column)
    return column


def _literal_info(found: FoundLiteral, in_list: str | None, lin: _Lineage) -> LiteralInfo:
    dialect = str(lin.dialect)
    other = found.other_side
    column_node = _column_node(other, lin)
    resolved = _resolve(column_node, lin) if column_node is not None else None
    typed = isinstance(found.node, exp.Cast)
    value = _literal_value(found)
    return LiteralInfo(
        id=found.id,
        scope=found.scope,
        value=value,
        is_string=isinstance(found.literal, exp.Literal) and found.literal.is_string,
        form=LiteralForm.TYPED if typed else LiteralForm.BARE,
        cast_type=found.node.to.sql(dialect=dialect) if typed else None,
        operator=found.operator,
        column_sql=generate(other, lin.dialect, pretty=False) if other is not None else None,
        column=resolved.ref if resolved is not None else None,
        wrapped=other is not None and not isinstance(other, exp.Column),
        in_list=in_list,
        context=list(found.context),
        wrapper_sql=wrapper_sql(found, lin.dialect),
        suggested_format=_suggested_format(found, value),
        compared_type=(
            _compared_type(other, resolved, lin, found.operator in _ELEMENT_OPERATORS)
            if other is not None
            else None
        ),
    )


def _literal_infos(found: list[FoundLiteral], lin: _Lineage) -> list[LiteralInfo]:
    """LiteralInfo per found literal; IN lists numbered per scope (`<scope>.IN<n>`)."""
    groups: dict[int, str] = {}
    per_scope: dict[str, int] = {}
    infos: list[LiteralInfo] = []
    for f in found:
        in_list = None
        if f.operator == "IN":
            key = id(f.predicate)
            if key not in groups:
                per_scope[f.scope] = per_scope.get(f.scope, 0) + 1
                groups[key] = f"{f.scope}.IN{per_scope[f.scope]}"
            in_list = groups[key]
        infos.append(_literal_info(f, in_list, lin))
    return infos


def _projection_output(node: exp.Expression) -> str | None:
    parent = node.parent
    if isinstance(parent, exp.Alias) and isinstance(parent.parent, exp.Select):
        if parent.arg_key == "expressions":
            return parent.alias
    return None


def _grain_candidate(
    found: FoundGrain, lin: _Lineage, column_units: dict[str, str]
) -> GrainCandidate:
    unit = found.unit
    if unit in column_units and isinstance(found.node.args.get("unit"), exp.Var | exp.Literal):
        unit = COLUMN_UNIT_PREFIX + column_units[unit]
    this = truncated_expression(found.node)
    column_node = this if isinstance(this, exp.Column) else this.find(exp.Column)
    resolved = _resolve(column_node, lin) if column_node is not None else None
    return GrainCandidate(
        id=found.id,
        scope=found.scope,
        unit=unit,
        column_sql=generate(this, lin.dialect, pretty=False),
        sql=generate(found.node, lin.dialect, pretty=False),
        column=resolved.ref if resolved is not None else None,
        column_type=_compared_type(this, resolved, lin) if column_node is not None else None,
        output=_projection_output(found.node),
        context=list(found.context),
    )


# ── Range candidates (ADR 0015) ─────────────────────────────────────────────────────


def _chain_top(predicate: exp.Expression) -> exp.Expression | None:
    """Top of the AND chain the predicate is a direct operand of (through parentheses)."""
    node = predicate
    while isinstance(node.parent, exp.And | exp.Paren):
        node = node.parent
    return node if node is not predicate else None


def _candidate(
    low: FoundLiteral, high: FoundLiteral, shape: RangeShape, infos: dict[str, LiteralInfo]
) -> RangeCandidate:
    return RangeCandidate(
        scope=low.scope,
        column_sql=infos[low.id].column_sql or "",
        start=low.id,
        end=high.id,
        shape=shape,
    )


def _between_pair(lits: list[FoundLiteral]) -> tuple[FoundLiteral, FoundLiteral] | None:
    lows = [f for f in lits if f.operator == "BETWEEN_LOW"]
    highs = [f for f in lits if f.operator == "BETWEEN_HIGH"]
    return (lows[0], highs[0]) if len(lows) == 1 and len(highs) == 1 else None


def _ranges(found: list[FoundLiteral], infos: dict[str, LiteralInfo]) -> list[RangeCandidate]:
    """Pair bounds that are direct operands of one AND chain (or one BETWEEN), on the same
    column expression, each bound holding exactly one literal."""
    by_predicate: dict[int, list[FoundLiteral]] = {}
    for f in found:
        by_predicate.setdefault(id(f.predicate), []).append(f)
    candidates: list[RangeCandidate] = []
    chains: dict[tuple[str, int, str | None], dict[str, list[FoundLiteral]]] = {}
    for lits in by_predicate.values():
        first = lits[0]
        if isinstance(first.predicate, exp.Between):
            pair = _between_pair(lits)
            if pair is not None:
                candidates.append(_candidate(*pair, RangeShape.INCLUSIVE, infos))
            continue
        top = _chain_top(first.predicate)
        if len(lits) != 1 or first.operator not in _LOW_OPS | _HIGH_OPS or top is None:
            continue
        side = "low" if first.operator in _LOW_OPS else "high"
        key = (first.scope, id(top), infos[first.id].column_sql)
        chains.setdefault(key, {"low": [], "high": []})[side].append(first)
    for bounds in chains.values():
        if len(bounds["low"]) == 1 and len(bounds["high"]) == 1:
            low, high = bounds["low"][0], bounds["high"][0]
            candidates.append(_candidate(low, high, _SHAPES[(low.operator, high.operator)], infos))
    order = {f.id: i for i, f in enumerate(found)}
    return sorted(candidates, key=lambda c: order[c.start])


# ── Joins ───────────────────────────────────────────────────────────────────────────


def _and_operands(condition: exp.Expression | None) -> list[exp.Expression]:
    if condition is None:
        return []
    if isinstance(condition, exp.Paren):
        return _and_operands(condition.this)
    if isinstance(condition, exp.And):
        return _and_operands(condition.left) + _and_operands(condition.right)
    return [condition]


def _column_source(column: exp.Column, sources: dict[str, _Source], lin: _Lineage) -> str | None:
    if column.table:
        key = _ident_key(column.args["table"])
        return key if key in sources else None
    owners = [k for k, s in sources.items() if _has_column(s, column.this, lin)]
    return owners[0] if len(owners) == 1 else None


_KeyIdents = list[tuple[exp.Expression, exp.Expression]]


def _join_keys(
    on: exp.Expression | None, right: str, sources: dict[str, _Source], lin: _Lineage
) -> tuple[str | None, _KeyIdents]:
    """(left alias key, equality pairs as identifiers) from the ON condition's top-level AND
    chain."""
    left: str | None = None
    keys: _KeyIdents = []
    for operand in _and_operands(on):
        if not isinstance(operand, exp.EQ):
            continue
        a, b = operand.left, operand.right
        if not (isinstance(a, exp.Column) and isinstance(b, exp.Column)):
            continue
        sa, sb = _column_source(a, sources, lin), _column_source(b, sources, lin)
        if sb == right and sa not in (None, right):
            lcol, rcol, lkey = a, b, sa
        elif sa == right and sb not in (None, right):
            lcol, rcol, lkey = b, a, sb
        else:
            continue
        left = left or lkey
        if lkey == left:
            keys.append((lcol.this, rcol.this))
    return left, keys


def _base_side(
    source: _Source, idents: list[exp.Expression], lin: _Lineage
) -> tuple[str | None, list[str]]:
    """(base table, key columns in it) behind one side of a join: the source itself when it
    is a base table; else the single base table every key column passes through from
    unchanged (lineage through CTEs / derived tables). (None, []) otherwise."""
    if source.parts is not None:
        return source.name, [_ident_key(i) for i in idents]
    refs = [_resolve_in(source, ident, lin, 0).ref for ident in idents]
    tables = {r.table for r in refs}
    if not refs or len(tables) != 1 or any(r.derived or r.table is None for r in refs):
        return None, []
    return refs[0].table, [r.column for r in refs]


def _join_type(join: exp.Join, has_condition: bool) -> str:
    side, kind = (join.side or "").lower(), (join.kind or "").lower()
    if kind in ("semi", "anti"):  # `LEFT SEMI JOIN` filters the driving rows: not outer
        return kind
    if side:
        return side
    if kind == "cross":
        return kind
    return "inner" if has_condition else "cross"


def _join_info(join: exp.Join, scope: str, lin: _Lineage) -> JoinInfo | None:
    select = join.parent
    sources = _sources(select, lin)
    right_key = _alias_key(join.this)
    on, using = join.args.get("on"), join.args.get("using") or []
    if using:
        left_key = None
        idents: _KeyIdents = [(u, u) for u in using]
        on_sql = f"USING ({', '.join(u.sql(dialect=str(lin.dialect)) for u in using)})"
    else:
        left_key, idents = _join_keys(on, right_key, sources, lin)
        on_sql = on.sql(dialect=str(lin.dialect)) if on is not None else ""
    from_ = select.args.get("from_")
    left_key = left_key or (_alias_key(from_.this) if from_ is not None else None)
    left, right = sources.get(left_key or ""), sources.get(right_key)
    if left is None or right is None:
        return None
    left_base, left_cols = _base_side(left, [a for a, _ in idents], lin)
    right_base, right_cols = _base_side(right, [b for _, b in idents], lin)
    both = left_base is not None and right_base is not None
    return JoinInfo(
        scope=scope,
        left_table=left.name,
        right_table=right.name,
        join_type=_join_type(join, on is not None or bool(using)),
        keys=[(_ident_key(a), _ident_key(b)) for a, b in idents],
        on_sql=on_sql,
        left_base_table=left_base,
        right_base_table=right_base,
        base_keys=list(zip(left_cols, right_cols, strict=True)) if both else [],
    )


def _joins(tree: exp.Expression, lin: _Lineage) -> list[JoinInfo]:
    joins: list[JoinInfo] = []
    for scope in scopes(tree):
        for node in _walk(scope, tree):
            if isinstance(node, exp.Join) and isinstance(node.parent, exp.Select):
                if (node.kind or "").upper() == "ARRAY":  # ClickHouse ARRAY JOIN: no table
                    continue
                info = _join_info(node, scope.name, lin)
                if info is not None:
                    joins.append(info)
    return joins


# ── Public API ──────────────────────────────────────────────────────────────────────


def _table_names(root: exp.Expression, lin: _Lineage, *, include_ctes: bool) -> list[str]:
    names: set[str] = set()
    for table in root.find_all(exp.Table):
        source = _source_of(table, lin)
        if source is not None and (include_ctes or source.parts is not None):
            names.add(source.name)
    return sorted(names)


def _lineage(tree: exp.Expression, dialect: Dialect, schema: Schema | None) -> _Lineage:
    return _Lineage(
        ctes=_cte_map(tree),
        schema=SchemaIndex(schema),
        dialect=dialect,
        with_=_top_with(tree),
        positions=_cte_positions(tree),
    )


def referenced_tables(sql: str, dialect: Dialect) -> list[str]:
    """Base tables a query reads (CTE names excluded), qualified as written with unquoted
    parts lower-cased. Used to fetch the schema first."""
    tree = parse(sql, dialect)
    return _table_names(tree, _lineage(tree, dialect, None), include_ctes=False)


def analyze(sql: str, dialect: Dialect, schema: Schema | None = None) -> QueryAnalysis:
    tree = parse(sql, dialect)
    reject_query_parameters(tree, dialect)
    lin = _lineage(tree, dialect, schema)
    found = find_literals(tree, dialect)
    literals = _literal_infos(found, lin)
    column_units = identifier_units(sql, dialect)
    return QueryAnalysis(
        dialect=dialect,
        ctes=[
            CteInfo(
                name=name,
                reads=_table_names(body, lin, include_ctes=True),
                # A ClickHouse scalar WITH (`WITH 'x' AS m`) names a value, not a query.
                outputs=list(body.named_selects) if isinstance(body, exp.Query) else [],
            )
            for name, body in lin.ctes.values()
        ],
        tables=_table_names(tree, lin, include_ctes=False),
        outputs=list(tree.named_selects),
        literals=literals,
        ranges=_ranges(found, {lit.id: lit for lit in literals}),
        joins=_joins(tree, lin),
        grains=[_grain_candidate(g, lin, column_units) for g in find_grains(tree, dialect)],
    )
