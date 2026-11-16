"""The ONLY place variable values become SQL. Values are always AST literal nodes —
never strings spliced into SQL text (DESIGN §14).
"""

from __future__ import annotations

import datetime as dt

from sqlglot import exp

from cteforge.domain.dialects import Dialect
from cteforge.domain.models import LiteralForm, VariableSpec
from cteforge.domain.variables import (
    PLACEHOLDER_FIELDS,
    DateRangeValue,
    TypedValue,
    VariableType,
    placeholder_name,
)

Replacement = exp.Expression | list[exp.Expression]


def _scalar(value: object) -> exp.Expression:
    if isinstance(value, bool):
        return exp.Boolean(this=value)
    if isinstance(value, int | float):
        if value < 0:
            return exp.Neg(this=exp.Literal.number(-value))
        return exp.Literal.number(value)
    if isinstance(value, dt.date):
        return exp.Literal.string(value.isoformat())
    return exp.Literal.string(str(value))


def _typed(node: exp.Expression, spec: VariableSpec, dialect: Dialect) -> exp.Expression:
    if spec.literal_form is LiteralForm.TYPED and spec.cast_type:
        return exp.Cast(this=node, to=exp.DataType.build(spec.cast_type, dialect=str(dialect)))
    return node


def build_replacements(
    spec: VariableSpec, value: TypedValue, source_dialect: Dialect
) -> dict[str, Replacement]:
    """Placeholder name → node(s) to put in its place. text_list yields a list."""
    if spec.type is VariableType.DATE_RANGE:
        assert isinstance(value, DateRangeValue)
        start_field, end_field = PLACEHOLDER_FIELDS[VariableType.DATE_RANGE]
        return {
            placeholder_name(spec.name, start_field): _typed(
                _scalar(value.start), spec, source_dialect
            ),
            placeholder_name(spec.name, end_field): _typed(
                _scalar(value.end), spec, source_dialect
            ),
        }
    if spec.type is VariableType.TEXT_LIST:
        assert isinstance(value, list)
        return {
            placeholder_name(spec.name): [_typed(_scalar(v), spec, source_dialect) for v in value]
        }
    return {placeholder_name(spec.name): _typed(_scalar(value), spec, source_dialect)}
