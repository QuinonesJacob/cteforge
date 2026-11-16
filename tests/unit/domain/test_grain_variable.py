"""grain variable type (DESIGN §4, M1.5): strict input, plain value, slot rules, codec."""

import pytest
from pydantic import ValidationError

from cteforge.domain.codec import definition_from_yaml, definition_to_yaml
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import VariableValueError
from cteforge.domain.models import Definition, VariableSlot, VariableSpec
from cteforge.domain.variables import (
    PLACEHOLDER_FIELDS,
    Grain,
    Role,
    VariableType,
    expected_format,
    to_plain,
    validate_value,
)


@pytest.mark.parametrize("raw", ["day", "week", "month", "quarter", "year"])
def test_accepts_exactly_the_five_grains(raw):
    value = validate_value("g", VariableType.GRAIN, raw)
    assert value is Grain(raw)
    assert to_plain(VariableType.GRAIN, value) == raw
    assert type(to_plain(VariableType.GRAIN, value)) is str


@pytest.mark.parametrize(
    "raw", ["MONTH", "Month", "Q", "monthly", "hour", "2026-Q3", " month", "", 1, None, ["day"]]
)
def test_rejects_anything_else_with_the_expected_format(raw):
    with pytest.raises(VariableValueError) as err:
        validate_value("time_grain", VariableType.GRAIN, raw)
    message = str(err.value)
    assert "'time_grain' (grain)" in message
    assert '"day" | "week" | "month" | "quarter" | "year"' in message


def test_expected_format_lists_the_grains():
    assert expected_format(VariableType.GRAIN) == (
        'one of "day" | "week" | "month" | "quarter" | "year" (lower case)'
    )
    assert PLACEHOLDER_FIELDS[VariableType.GRAIN] == ("",)


def _grain_spec(**overrides) -> VariableSpec:
    fields = {
        "name": "time_grain",
        "type": VariableType.GRAIN,
        "role": Role.CHANGEABLE,
        "original_value": "month",
        "slots": [
            VariableSlot(placeholder="time_grain", literals=["cte.G1"], original_text="month")
        ],
    }
    return VariableSpec(**{**fields, **overrides})


def test_grain_slots_need_one_id_and_the_original_unit():
    assert _grain_spec().slots[0].original_text == "month"
    with pytest.raises(ValidationError, match="one grain ID and its original unit"):
        _grain_spec(slots=[VariableSlot(placeholder="time_grain", literals=["cte.G1"])])
    with pytest.raises(ValidationError, match="one grain ID and its original unit"):
        _grain_spec(
            slots=[
                VariableSlot(
                    placeholder="time_grain", literals=["a.G1", "b.G1"], original_text="month"
                )
            ]
        )


def test_grain_takes_no_format_or_unit():
    with pytest.raises(ValidationError, match="format applies to date"):
        _grain_spec(format="%Y")
    with pytest.raises(ValidationError, match="unit applies to number"):
        _grain_spec(unit="days")


def test_grain_definition_roundtrips_through_yaml():
    definition = Definition(
        id="monthly",
        title="Monthly",
        source_dialect=Dialect.DUCKDB,
        template="SELECT DATE_TRUNC('MONTH', created_at) AS m FROM orders",
        variables=[_grain_spec()],
    )
    assert definition_from_yaml(definition_to_yaml(definition)) == definition
