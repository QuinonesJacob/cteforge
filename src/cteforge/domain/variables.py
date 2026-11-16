"""Variable types and their strict input formats (DESIGN §4, ADR 0006).

This is the ONLY place variable values are validated. Rendering (templating/) calls
`validate_value` and trusts its output.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
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
    GRAIN = "grain"  # M1.5: rebuilds date truncation nodes, never a value placeholder.


class Role(StrEnum):
    CHANGEABLE = "changeable"
    FIXED = "fixed"


class Grain(StrEnum):
    """Values of a `grain` variable (DESIGN §4). Week start depends on the warehouse (§13)."""

    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


GRAIN_INPUT_RULE = "one of " + " | ".join(f'"{g}"' for g in Grain) + " (lower case)"


class RangeShape(StrEnum):
    """How a date_range appeared in the trusted SQL. Decides what `end` means."""

    HALF_OPEN = "half_open"  # col >= start AND col < end   [start, end)
    INCLUSIVE = "inclusive"  # col BETWEEN start AND end, or >= / <=   [start, end]
    EXCLUSIVE = "exclusive"  # col > start AND col < end   (start, end)
    START_EXCLUSIVE = "start_exclusive"  # col > start AND col <= end   (start, end]


# One *place* of a variable = one occurrence of its filter in the SQL: a pair of bounds
# (date_range), one whole IN list (text_list) or one literal. Each field is one slot of the
# place and names its placeholder suffix. A variable may fill several places (ADR 0015).
PLACEHOLDER_FIELDS: dict[VariableType, tuple[str, ...]] = {
    VariableType.DATE_RANGE: ("start", "end"),
    VariableType.DATE: ("",),
    VariableType.TEXT_LIST: ("",),
    VariableType.TEXT: ("",),
    VariableType.NUMBER: ("",),
    VariableType.BOOLEAN: ("",),
    VariableType.GRAIN: ("",),  # One place = one truncation node (a `<scope>.G<n>` ID).
}


def placeholder_name(variable: str, field: str = "", place: int = 1) -> str:
    """Placeholder identifier used in stored templates (ADR 0011).

    Place 1: `period__start`, `regions`. Place n ≥ 2 (ADR 0015, multi-place variables):
    `period__2__start`, `regions__2`. Variable names never contain `__`, so these can't
    collide with another variable.
    """
    parts = [variable, *([str(place)] if place > 1 else []), *([field] if field else [])]
    return "__".join(parts)


class DateRangeValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start: dt.date
    end: dt.date

    @model_validator(mode="after")
    def _ordered(self) -> DateRangeValue:
        if self.end < self.start:
            raise ValueError("end must not be before start")
        return self


NUMBER_INPUT_RULE = (
    "a JSON number (not a string, not NaN/Infinity), e.g. 100 or 1000.5; "
    "agents convert units themselves (see the variable's unit)"
)

_TEXT = TypeAdapter(str, config=ConfigDict(strict=True))
_BOOL = TypeAdapter(StrictBool)

_FORMATS: dict[VariableType, str] = {
    VariableType.DATE_RANGE: '{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}',
    VariableType.DATE: '"YYYY-MM-DD"',
    VariableType.TEXT_LIST: '["a", "b"] (non-empty list of strings)',
    VariableType.TEXT: '"text"',
    VariableType.NUMBER: NUMBER_INPUT_RULE,
    VariableType.BOOLEAN: "true or false",
    VariableType.GRAIN: GRAIN_INPUT_RULE,
}

# Numbers are validated into Decimal so rendering keeps exact text (1000.50, ADR 0014).
# Grain values are Grain members (a str subclass).
TypedValue = DateRangeValue | dt.date | list[str] | str | Decimal | bool


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


def _number(raw: Any) -> Decimal:
    if isinstance(raw, bool) or not isinstance(raw, int | float | Decimal):
        raise ValueError("expected a JSON number, not a string or other value")
    number = Decimal(repr(raw)) if isinstance(raw, float) else Decimal(raw)
    if not number.is_finite():
        raise ValueError("NaN and Infinity are not numbers SQL can compare to")
    return number


def _grain(raw: Any) -> Grain:
    # Strict: exactly the lower-case names. "Q", "MONTH", "monthly", "2026-Q3" are rejected.
    if not isinstance(raw, str) or raw not in {g.value for g in Grain}:
        raise ValueError("expected a grain name")
    return Grain(raw)


def number_from_text(text: str) -> Decimal:
    """A numeric literal's text as written in SQL ("1000.50", "1e3", "-5") → Decimal."""
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"{text!r} is not a number") from exc
    if not number.is_finite():
        raise ValueError(f"{text!r} is not a finite number")
    return number


def read_date(text: str, fmt: str | None) -> dt.date:
    """Date text as written in SQL → date. `fmt` is a strftime pattern (ADR 0014);
    None means ISO YYYY-MM-DD. Raises ValueError when the text doesn't match."""
    if fmt is None:
        if len(text) != 10:
            raise ValueError(f"{text!r} is not a plain YYYY-MM-DD date")
        return dt.date.fromisoformat(text)
    parsed = dt.datetime.strptime(text, fmt)
    if parsed.time() != dt.time():
        raise ValueError(f"{text!r} has a time of day; date variables hold dates only")
    return parsed.date()


def write_date(value: dt.date, fmt: str | None) -> str:
    """Inverse of read_date: ISO when `fmt` is None, else strftime(fmt)."""
    return value.isoformat() if fmt is None else value.strftime(fmt)


def validate_value(name: str, vtype: VariableType, raw: Any) -> TypedValue:
    """Validate a raw value (from JSON/YAML/an LLM) against the variable's strict format.

    Raises VariableValueError with the expected format, so an agent can self-correct.
    """
    try:
        match vtype:
            case VariableType.DATE_RANGE:
                if not isinstance(raw, dict):
                    raise ValueError("expected an object with start and end")
                missing = [k for k in ("start", "end") if k not in raw]
                if missing:
                    raise ValueError(" and ".join(f"missing '{k}'" for k in missing))
                extra = sorted(str(k) for k in raw if k not in ("start", "end"))
                if extra:
                    raise ValueError(f"unexpected key(s) {extra}; only 'start' and 'end'")
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
                return _number(raw)
            case VariableType.BOOLEAN:
                return _BOOL.validate_python(raw)
            case VariableType.GRAIN:
                return _grain(raw)
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
    if vtype is VariableType.NUMBER:
        assert isinstance(value, Decimal)
        return int(value) if value == value.to_integral_value() else float(value)
    if vtype is VariableType.GRAIN:
        return str(value)
    return value
