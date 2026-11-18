"""Warehouse type string → which variable types make sense (ADR 0006).

Pure: callers pass the type string they got from the warehouse.
"""

from __future__ import annotations

from sqlglot import exp
from sqlglot.errors import ParseError

from cteforge.domain.dialects import Dialect
from cteforge.domain.variables import VariableType

_DATE_TYPES = {exp.DataType.Type.DATE, exp.DataType.Type.DATE32}
_TIMESTAMP_TYPES = exp.DataType.TEMPORAL_TYPES - _DATE_TYPES
# BPCHAR: blank-padded CHAR as Postgres / Redshift catalogs may spell it.
_TEXT_TYPES = exp.DataType.TEXT_TYPES | {exp.DataType.Type.BPCHAR}
# ClickHouse FixedString, Enum8 / Enum16 and UUID columns compare with string literals
# (`status = 'error'`, `id = '…-…'`), so a text value filters them (ADR 0025).
_CLICKHOUSE_TEXT_TYPES = {
    exp.DataType.Type.FIXEDSTRING,
    exp.DataType.Type.ENUM,
    exp.DataType.Type.ENUM8,
    exp.DataType.Type.ENUM16,
    exp.DataType.Type.UUID,
}
# MySQL ENUM / SET columns compare with string literals (`status = 'paid'`); YEAR columns
# with numbers (`y = 2026`). JSON / BLOB / BINARY / GEOMETRY have no variable type (ADR 0028).
_MYSQL_TEXT_TYPES = {exp.DataType.Type.ENUM, exp.DataType.Type.SET}
_MYSQL_NUMBER_TYPES = {exp.DataType.Type.YEAR}
_NUMERIC_TYPES = exp.DataType.NUMERIC_TYPES
_BOOL_TYPES = {exp.DataType.Type.BOOLEAN}


def _mysql_flag(dtype: exp.DataType) -> bool:
    """MySQL TINYINT(1): what BOOL / BOOLEAN columns are stored as (information_schema
    reports `tinyint(1)`). It holds any TINYINT value, so it is a number and a boolean."""
    if dtype.this is not exp.DataType.Type.TINYINT or len(dtype.expressions) != 1:
        return False
    width = dtype.expressions[0]
    inner = width.this if isinstance(width, exp.DataTypeParam) else width
    return isinstance(inner, exp.Literal) and inner.name == "1"


def data_type(type_str: str, dialect: Dialect) -> exp.DataType | None:
    """Parse a warehouse type string; None if sqlglot doesn't understand it."""
    try:
        return exp.DataType.build(type_str, dialect=str(dialect))
    except (ParseError, ValueError):
        return None


def _value_type(dtype: exp.DataType) -> exp.DataType:
    """The type a value is compared as. ClickHouse wrappers are unwrapped:
    LowCardinality(T) → T (Nullable(T) is already T with a nullable flag in sqlglot), and
    SimpleAggregateFunction(f, T) → T (the column stores plain T values)."""
    while dtype.expressions:
        if dtype.this is exp.DataType.Type.LOWCARDINALITY:
            inner = dtype.expressions[0]
        elif dtype.this is exp.DataType.Type.SIMPLEAGGREGATEFUNCTION:
            inner = dtype.expressions[-1]
        else:
            break
        if not isinstance(inner, exp.DataType):
            break
        dtype = inner
    return dtype


def element_type(dtype: exp.DataType) -> exp.DataType | None:
    """Element type of an Array(T) (through LowCardinality / Nullable), the value type of a
    Map(K, V); None for other types. What `has(arr, 'x')`, `arr[1]`, `m['k']` compare."""
    dtype = _value_type(dtype)
    if dtype.this is exp.DataType.Type.ARRAY and dtype.expressions:
        inner = dtype.expressions[0]
    elif dtype.this is exp.DataType.Type.MAP and len(dtype.expressions) == 2:
        inner = dtype.expressions[1]
    else:
        return None
    return inner if isinstance(inner, exp.DataType) else None


def compatible_variable_types(type_str: str | None, dialect: Dialect) -> set[VariableType]:
    """Variable types that can filter a column of this type. Empty set = unknown type."""
    if type_str is None:
        return set()
    dtype = data_type(type_str, dialect)
    if dtype is None:
        return set()
    kind = _value_type(dtype).this
    if kind in _DATE_TYPES or kind in _TIMESTAMP_TYPES:
        return {VariableType.DATE, VariableType.DATE_RANGE}
    if kind in _TEXT_TYPES or (dialect is Dialect.CLICKHOUSE and kind in _CLICKHOUSE_TEXT_TYPES):
        return {VariableType.TEXT, VariableType.TEXT_LIST}
    if dialect is Dialect.MYSQL:
        if kind in _MYSQL_TEXT_TYPES:
            return {VariableType.TEXT, VariableType.TEXT_LIST}
        if _mysql_flag(dtype):
            return {VariableType.NUMBER, VariableType.BOOLEAN}
        if kind in _MYSQL_NUMBER_TYPES:
            return {VariableType.NUMBER}
    if kind in _NUMERIC_TYPES:
        return {VariableType.NUMBER}
    if kind in _BOOL_TYPES:
        return {VariableType.BOOLEAN}
    return set()
