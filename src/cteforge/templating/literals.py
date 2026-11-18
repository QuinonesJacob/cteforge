"""The ONLY place variable values become SQL. Values are always AST literal nodes —
never strings spliced into SQL text (DESIGN §14).

Each slot of a variable is filled the way its literal was written in the trusted SQL:
bare or typed (cast to the slot's cast_type), dates in the slot's format, numbers with
their exact original text when the value is unchanged (ADR 0014).

A grain value (M1.5) is never put into a placeholder: `build_truncation` rebuilds the
whole truncation node with the new unit (ADR 0011).
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal

from sqlglot import exp
from sqlglot.errors import ParseError

from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import RenderError
from cteforge.domain.models import LiteralForm, VariableSlot, VariableSpec
from cteforge.domain.variables import (
    DateRangeValue,
    Grain,
    TypedValue,
    VariableType,
    number_from_text,
    write_date,
)
from cteforge.parsing.analyze import (
    CLICKHOUSE_INTERVAL_FUNC,
    CLICKHOUSE_NAME_KEY,
    CLICKHOUSE_WEEK_FUNC,
    mysql_period,
    parse,
)

Replacement = exp.Expression | list[exp.Expression]

_NUMBER_TEXT = re.compile(r"^\d+(\.\d*)?([eE][+-]?\d+)?$")
# sqlglot's zone-naive timestamp types. Spelled out as TIMESTAMPNTZ when transpiling, so
# every target writes a naive type (Snowflake TIMESTAMP_NTZ, BigQuery DATETIME, Databricks
# TIMESTAMP_NTZ) instead of a generic TIMESTAMP that some targets read as zone-aware.
_NAIVE_TIMESTAMPS = {exp.DataType.Type.TIMESTAMP, exp.DataType.Type.DATETIME}
# ClickHouse DateTime / DateTime64 carry a precision and an optional time zone as type
# parameters, which other dialects cannot read (sqlglot would write `DATETIME64(3, 'UTC')`
# for DuckDB). Transpiled: with a time zone → zone-aware TIMESTAMPTZ, without → naive.
_CLICKHOUSE_DATETIMES = {exp.DataType.Type.DATETIME, exp.DataType.Type.DATETIME64}


def _clickhouse_portable(dtype: exp.DataType) -> exp.DataType:
    if dtype.this is exp.DataType.Type.LOWCARDINALITY and dtype.expressions:
        inner = dtype.expressions[0]
        dtype = inner if isinstance(inner, exp.DataType) else dtype
    if dtype.this not in _CLICKHOUSE_DATETIMES:
        return dtype
    zoned = any(
        isinstance(p, exp.DataTypeParam) and isinstance(p.this, exp.Literal) and p.this.is_string
        for p in dtype.expressions
    )
    kind = exp.DataType.Type.TIMESTAMPTZ if zoned else exp.DataType.Type.TIMESTAMPNTZ
    return exp.DataType(this=kind)


def _number(value: Decimal, original_text: str | None) -> exp.Expression:
    """Exact numeric literal: the original text when the value is unchanged ("1000.50",
    "1e3"), else the plain decimal. Never an identifier (no inf/NaN)."""
    if not value.is_finite():  # validate_value already rejects these.
        raise RenderError(f"Cannot render non-finite number {value}")
    text = format(value, "f")
    if original_text is not None and number_from_text(original_text) == value:
        text = original_text
    negative = text.startswith("-")
    digits = text.lstrip("-")
    if not _NUMBER_TEXT.match(digits):
        raise RenderError(f"Refusing to render {text!r} as a number")  # pragma: no cover
    node: exp.Expression = exp.Literal.number(digits)
    return exp.Neg(this=node) if negative else node


def cast_type(
    slot: VariableSlot, spec: VariableSpec, source: Dialect, target: Dialect | None = None
) -> exp.DataType:
    """The type a typed slot is cast to: its cast_type read in the source dialect (so
    naive/zone-aware semantics carry over), else the column's warehouse type (DESIGN §13).
    When transpiling, zone-naive timestamps are spelled explicitly naive."""
    for candidate in (slot.cast_type, spec.column_type):
        if not candidate:
            continue
        try:
            dtype = exp.DataType.build(candidate, dialect=str(source))
        except (ParseError, ValueError):
            continue
        if target is not None and target is not source and source is Dialect.CLICKHOUSE:
            return _clickhouse_portable(dtype)
        if target is not None and target is not source and dtype.this in _NAIVE_TIMESTAMPS:
            dtype = dtype.copy()
            dtype.set("this", exp.DataType.Type.TIMESTAMPNTZ)
        return dtype
    raise RenderError(f"'{spec.name}': no usable type for typed slot {slot.placeholder}")


def _typed(
    node: exp.Expression,
    slot: VariableSlot,
    spec: VariableSpec,
    source: Dialect,
    target: Dialect | None,
) -> exp.Expression:
    if slot.literal_form is LiteralForm.TYPED:
        return exp.Cast(this=node, to=cast_type(slot, spec, source, target))
    return node


def _scalar(spec: VariableSpec, slot: VariableSlot, value: TypedValue) -> exp.Expression:
    if isinstance(value, bool):
        return exp.Boolean(this=value)
    if isinstance(value, Decimal):
        return _number(value, slot.original_text)
    if isinstance(value, DateRangeValue):
        return exp.Literal.string(write_date(getattr(value, slot.field), slot.format))
    if isinstance(value, dt.date):
        return exp.Literal.string(write_date(value, slot.format))
    if isinstance(value, str):
        return exp.Literal.string(value)
    raise RenderError(f"'{spec.name}': cannot render {type(value).__name__}")  # pragma: no cover


def _unit_node(grain: Grain, source: Dialect) -> exp.Expression:
    """The unit as the source dialect's parser builds it, so a rebuilt node equals what
    parsing the rendered SQL gives back. BigQuery's native WEEK is WEEK(SUNDAY) there."""
    if grain is Grain.WEEK and source is Dialect.BIGQUERY:
        return exp.WeekStart(this=exp.Var(this="SUNDAY"))
    return exp.Var(this=grain.value.upper())


# ClickHouse's own function per grain; week is toMonday (Monday, the native week shared
# with date_trunc('week') and toStartOfInterval WEEK; DESIGN §13).
_CLICKHOUSE_TRUNC_FUNCS = {
    Grain.DAY: "toStartOfDay",
    Grain.WEEK: "toMonday",
    Grain.MONTH: "toStartOfMonth",
    Grain.QUARTER: "toStartOfQuarter",
    Grain.YEAR: "toStartOfYear",
}


def _clickhouse_truncation(node: exp.Expression, grain: Grain) -> exp.Expression | None:
    """A ClickHouse truncation rebuilt in the SQL's own family, as ClickHouse's parser
    builds it: toStartOfInterval stays toStartOfInterval (same result type as its input);
    toStartOf<Unit> / toMonday / toStartOfWeek become the grain's toStartOf<Unit> /
    toMonday (a TimestampTrunc named in meta, written by name by `generate`). None for
    date_trunc / dateTrunc, which the generic rebuild handles."""
    if isinstance(node, exp.Anonymous) and node.name == CLICKHOUSE_INTERVAL_FUNC:
        value, interval, *rest = node.expressions
        assert isinstance(interval, exp.Interval)
        rebuilt = exp.Interval(this=interval.this, unit=exp.Var(this=grain.value.upper()))
        return exp.Anonymous(this=CLICKHOUSE_INTERVAL_FUNC, expressions=[value, rebuilt, *rest])
    if isinstance(node, exp.Anonymous) and node.name == CLICKHOUSE_WEEK_FUNC:
        args = node.expressions
        this, zone = args[0], (args[2] if len(args) > 2 else None)
    elif isinstance(node, exp.TimestampTrunc) and node.meta.get(CLICKHOUSE_NAME_KEY):
        this, zone = node.this, node.args.get("zone")
    else:
        return None
    rebuilt = exp.TimestampTrunc(this=this, unit=exp.Var(this=grain.value.upper()), zone=zone)
    rebuilt.meta[CLICKHOUSE_NAME_KEY] = _CLICKHOUSE_TRUNC_FUNCS[grain]
    return rebuilt


# MySQL's DATE_FORMAT family (ADR 0028): the period start of the grain, written as the
# same 'YYYY-MM-DD' text the trusted SQL's DATE_FORMAT returns. Constant SQL (no values);
# `_MYSQL_VALUE` stands for the truncated expression. Read with `parsing.analyze.parse`,
# so the rebuilt node equals the rendered SQL parsed again. Week = Monday (WEEKDAY() is 0
# on Monday; DESIGN §13).
_MYSQL_VALUE = "cteforge_truncated_value"
_MYSQL_PERIOD_SQL = {
    Grain.DAY: "SELECT DATE_FORMAT(cteforge_truncated_value, '%Y-%m-%d')",
    Grain.WEEK: (
        "SELECT DATE_FORMAT(cteforge_truncated_value - INTERVAL "
        "WEEKDAY(cteforge_truncated_value) DAY, '%Y-%m-%d')"
    ),
    Grain.MONTH: "SELECT DATE_FORMAT(cteforge_truncated_value, '%Y-%m-01')",
    Grain.QUARTER: (
        "SELECT DATE_FORMAT(MAKEDATE(YEAR(cteforge_truncated_value), 1) + INTERVAL "
        "QUARTER(cteforge_truncated_value) - 1 QUARTER, '%Y-%m-%d')"
    ),
    Grain.YEAR: "SELECT DATE_FORMAT(cteforge_truncated_value, '%Y-01-01')",
}
# Operands that need no parentheses when the value is the left side of `- INTERVAL`.
_MYSQL_ATOMS = (exp.Column, exp.Func, exp.Literal, exp.Paren)


def _mysql_truncation(node: exp.Expression, grain: Grain) -> exp.Expression | None:
    """MySQL DATE_FORMAT(x, '<period start>') rebuilt for `grain` in the same family (same
    'YYYY-MM-DD[ 00:00:00]' text result, time suffix kept). Week and quarter use the value
    twice; the first use is the original node (moved), the others copies. Render rebuilds
    nested truncations first, so the copies carry them too. None for other nodes."""
    period = mysql_period(node)
    if period is None:
        return None
    suffix, value = period.suffix, period.value
    rebuilt = parse(_MYSQL_PERIOD_SQL[grain], Dialect.MYSQL).selects[0]
    uses = [c for c in rebuilt.find_all(exp.Column) if c.name == _MYSQL_VALUE]
    for n, use in enumerate(uses):
        replacement = value if n == 0 else value.copy()
        if not isinstance(replacement, _MYSQL_ATOMS) and isinstance(use.parent, exp.Sub):
            replacement = exp.Paren(this=replacement)
        use.replace(replacement)
    fmt = rebuilt.args["format"]
    rebuilt.set("format", exp.Literal.string(fmt.name + suffix))
    return rebuilt


def build_truncation(
    node: exp.Expression, grain: Grain, source: Dialect, target: Dialect | None = None
) -> exp.Expression:
    """A new truncation node of the same kind (DateTrunc / TimestampTrunc / DatetimeTrunc)
    over the same arguments, truncating to `grain`. The node's own constructor normalizes
    the unit (DateTrunc keeps a string, the others a Var), as the parser does. The
    arguments are moved, not copied, so nested truncations stay addressable."""
    if (target or source) is Dialect.DATABRICKS and isinstance(node, exp.DateTrunc):
        if grain is Grain.DAY:
            # DateTrunc is TRUNC(date, fmt) in Databricks, which documents only year /
            # quarter / month / week formats.
            raise RenderError(
                "Databricks TRUNC(date, fmt) has no 'day' format; write DATE_TRUNC in the "
                "trusted SQL to allow grain 'day'"
            )
    if source is Dialect.CLICKHOUSE:
        rebuilt = _clickhouse_truncation(node, grain)
        if rebuilt is not None:
            return rebuilt
    if source is Dialect.MYSQL:
        rebuilt = _mysql_truncation(node, grain)
        if rebuilt is not None:
            return rebuilt
    args = {k: v for k, v in node.args.items() if k != "unit"}
    rebuilt = type(node)(**args, unit=_unit_node(grain, source))
    if CLICKHOUSE_NAME_KEY in node.meta:  # date_trunc stays date_trunc (parsing.generate)
        rebuilt.meta[CLICKHOUSE_NAME_KEY] = node.meta[CLICKHOUSE_NAME_KEY]
    return rebuilt


def build_replacements(
    spec: VariableSpec,
    value: TypedValue,
    source_dialect: Dialect,
    target_dialect: Dialect | None = None,
) -> dict[str, Replacement]:
    """Placeholder name → node(s) to put in its place, one entry per slot.
    text_list yields a list of nodes (the whole IN list). grain yields nothing: it has no
    placeholder (render rebuilds its truncation nodes with `build_truncation`)."""
    out: dict[str, Replacement] = {}
    if spec.type is VariableType.GRAIN:
        return out
    for slot in spec.slots:
        if spec.type is VariableType.TEXT_LIST:
            assert isinstance(value, list)
            out[slot.placeholder] = [
                _typed(exp.Literal.string(v), slot, spec, source_dialect, target_dialect)
                for v in value
            ]
        else:
            node = _scalar(spec, slot, value)
            out[slot.placeholder] = _typed(node, slot, spec, source_dialect, target_dialect)
    return out
