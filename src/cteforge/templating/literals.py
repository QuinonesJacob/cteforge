"""The ONLY place variable values become SQL. Values are always AST literal nodes —
never strings spliced into SQL text (DESIGN §14).

Each slot of a variable is filled the way its literal was written in the trusted SQL:
bare or typed (cast to the slot's cast_type), dates in the slot's format, numbers with
their exact original text when the value is unchanged (ADR 0014).
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
    TypedValue,
    VariableType,
    number_from_text,
    write_date,
)

Replacement = exp.Expression | list[exp.Expression]

_NUMBER_TEXT = re.compile(r"^\d+(\.\d*)?([eE][+-]?\d+)?$")
# sqlglot's zone-naive timestamp types. Spelled out as TIMESTAMPNTZ when transpiling, so
# every target writes a naive type (Snowflake TIMESTAMP_NTZ, BigQuery DATETIME, Databricks
# TIMESTAMP_NTZ) instead of a generic TIMESTAMP that some targets read as zone-aware.
_NAIVE_TIMESTAMPS = {exp.DataType.Type.TIMESTAMP, exp.DataType.Type.DATETIME}


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


def build_replacements(
    spec: VariableSpec,
    value: TypedValue,
    source_dialect: Dialect,
    target_dialect: Dialect | None = None,
) -> dict[str, Replacement]:
    """Placeholder name → node(s) to put in its place, one entry per slot.
    text_list yields a list of nodes (the whole IN list)."""
    out: dict[str, Replacement] = {}
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
