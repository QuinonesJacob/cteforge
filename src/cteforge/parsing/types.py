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
_TEXT_TYPES = exp.DataType.TEXT_TYPES
_NUMERIC_TYPES = exp.DataType.NUMERIC_TYPES
_BOOL_TYPES = {exp.DataType.Type.BOOLEAN}


def data_type(type_str: str, dialect: Dialect) -> exp.DataType | None:
    """Parse a warehouse type string; None if sqlglot doesn't understand it."""
    try:
        return exp.DataType.build(type_str, dialect=str(dialect))
    except (ParseError, ValueError):
        return None


def compatible_variable_types(type_str: str | None, dialect: Dialect) -> set[VariableType]:
    """Variable types that can filter a column of this type. Empty set = unknown type."""
    if type_str is None:
        return set()
    dtype = data_type(type_str, dialect)
    if dtype is None:
        return set()
    kind = dtype.this
    if kind in _DATE_TYPES or kind in _TIMESTAMP_TYPES:
        return {VariableType.DATE, VariableType.DATE_RANGE}
    if kind in _TEXT_TYPES:
        return {VariableType.TEXT, VariableType.TEXT_LIST}
    if kind in _NUMERIC_TYPES:
        return {VariableType.NUMBER}
    if kind in _BOOL_TYPES:
        return {VariableType.BOOLEAN}
    return set()
