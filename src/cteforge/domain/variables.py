"""Variable types and their strict input formats (DESIGN §4, ADR 0006).

This is the ONLY place variable values are validated. Rendering (templating/) calls
`validate_value` and trusts its output.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    StrictBool,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from cteforge.domain.errors import VariableValueError


class VariableType(StrEnum):
    DATE_RANGE = "date_range"
    DATE = "date"
    TEXT_LIST = "text_list"
    TEXT = "text"
    NUMBER = "number"
    BOOLEAN = "boolean"
    # GRAIN = "grain"  — M1.5: rebuilds the truncation node, never a placeholder.


class Role(StrEnum):
    CHANGEABLE = "changeable"
    FIXED = "fixed"


class RangeShape(StrEnum):
    """How a date_range appeared in the trusted SQL. Decides what `end` means."""

    HALF_OPEN = "half_open"  # col >= start AND col < end   (end exclusive)
    INCLUSIVE = "inclusive"  # col BETWEEN start AND end, or >= / <=  (end inclusive)


# How many literals in the SQL each type replaces, and the placeholder suffixes used.
PLACEHOLDER_FIELDS: dict[VariableType, tuple[str, ...]] = {
    VariableType.DATE_RANGE: ("start", "end"),
    VariableType.DATE: ("",),
    VariableType.TEXT_LIST: ("",),
    VariableType.TEXT: ("",),
    VariableType.NUMBER: ("",),
    VariableType.BOOLEAN: ("",),
}


def placeholder_name(variable: str, field: str = "") -> str:
    """Placeholder identifier used in stored templates (ADR 0011): `period__start`."""
    return f"{variable}__{field}" if field else variable


class DateRangeValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start: dt.date
    end: dt.date

    @model_validator(mode="after")
    def _ordered(self) -> DateRangeValue:
        if self.end < self.start:
            raise ValueError("end must not be before start")
        return self


_TEXT = TypeAdapter(str, config=ConfigDict(strict=True))
_BOOL = TypeAdapter(StrictBool)

_FORMATS: dict[VariableType, str] = {
    VariableType.DATE_RANGE: '{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}',
    VariableType.DATE: '"YYYY-MM-DD"',
    VariableType.TEXT_LIST: '["a", "b"] (non-empty list of strings)',
    VariableType.TEXT: '"text"',
    VariableType.NUMBER: "a number, e.g. 100 or 0.5",
    VariableType.BOOLEAN: "true or false",
}

TypedValue = DateRangeValue | dt.date | list[str] | str | int | float | bool


def expected_format(vtype: VariableType) -> str:
    return _FORMATS[vtype]


def _iso_date(raw: Any) -> dt.date:
    # Accept only real dates or exact ISO strings; reject datetimes and "2026-Q3".
    if isinstance(raw, dt.datetime):
        raise ValueError("expected a date, got a datetime")
    if isinstance(raw, dt.date):
        return raw
    if not isinstance(raw, str) or len(raw) != 10:
        raise ValueError("expected an ISO date string YYYY-MM-DD")
    return dt.date.fromisoformat(raw)


def validate_value(name: str, vtype: VariableType, raw: Any) -> TypedValue:
    """Validate a raw value (from JSON/YAML/an LLM) against the variable's strict format.

    Raises VariableValueError with the expected format, so an agent can self-correct.
    """
    try:
        match vtype:
            case VariableType.DATE_RANGE:
                if not isinstance(raw, dict):
                    raise ValueError("expected an object with start and end")
                return DateRangeValue(
                    start=_iso_date(raw.get("start")),
                    end=_iso_date(raw.get("end")),
                    **{k: v for k, v in raw.items() if k not in ("start", "end")},
                )
            case VariableType.DATE:
                return _iso_date(raw)
            case VariableType.TEXT_LIST:
                if not isinstance(raw, list) or not raw:
                    raise ValueError("expected a non-empty list")
                return [_TEXT.validate_python(v) for v in raw]
            case VariableType.TEXT:
                return _TEXT.validate_python(raw)
            case VariableType.NUMBER:
                if isinstance(raw, bool) or not isinstance(raw, int | float):
                    raise ValueError("expected a number")
                return raw
            case VariableType.BOOLEAN:
                return _BOOL.validate_python(raw)
    except (ValueError, TypeError, ValidationError) as exc:
        reason = exc.errors()[0]["msg"] if isinstance(exc, ValidationError) else str(exc)
        raise VariableValueError(
            f"Invalid value for '{name}' ({vtype}): {raw!r}. "
            f"Expected {expected_format(vtype)}. Reason: {reason}"
        ) from exc
    raise VariableValueError(f"Unsupported variable type: {vtype}")  # pragma: no cover


def to_plain(vtype: VariableType, value: TypedValue) -> Any:
    """Typed value → plain JSON/YAML-friendly value (inverse of validate_value)."""
    if vtype is VariableType.DATE_RANGE:
        assert isinstance(value, DateRangeValue)
        return {"start": value.start.isoformat(), "end": value.end.isoformat()}
    if vtype is VariableType.DATE:
        assert isinstance(value, dt.date)
        return value.isoformat()
    return value
