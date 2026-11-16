import datetime as dt
from decimal import Decimal

import pytest

from cteforge.domain.errors import VariableValueError
from cteforge.domain.variables import (
    DateRangeValue,
    VariableType,
    placeholder_name,
    read_date,
    to_plain,
    validate_value,
    write_date,
)


def test_date_range_accepts_iso_dates():
    value = validate_value(
        "p", VariableType.DATE_RANGE, {"start": "2026-07-01", "end": "2026-10-01"}
    )
    assert value == DateRangeValue(start=dt.date(2026, 7, 1), end=dt.date(2026, 10, 1))


@pytest.mark.parametrize(
    "raw",
    [
        "2026-Q3",
        {"start": "2026-Q3", "end": "2026-10-01"},
        {"start": "2026-07-01"},
        {"start": "2026-10-01", "end": "2026-07-01"},
        {"start": "2026-07-01", "end": "2026-10-01", "extra": 1},
        {"start": "2026-07-01T00:00:00", "end": "2026-10-01"},
    ],
)
def test_date_range_rejects_non_iso_and_bad_shapes(raw):
    with pytest.raises(VariableValueError, match="Expected"):
        validate_value("period", VariableType.DATE_RANGE, raw)


def test_error_message_names_expected_format():
    with pytest.raises(VariableValueError) as err:
        validate_value("period", VariableType.DATE_RANGE, "2026-Q3")
    assert '{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}' in str(err.value)


@pytest.mark.parametrize(
    ("vtype", "raw"),
    [
        (VariableType.TEXT_LIST, []),
        (VariableType.TEXT_LIST, ["a", 1]),
        (VariableType.TEXT, 5),
        (VariableType.NUMBER, True),
        (VariableType.NUMBER, "5"),
        (VariableType.BOOLEAN, "true"),
        (VariableType.DATE, "07/01/2026"),
    ],
)
def test_rejects_wrong_types(vtype, raw):
    with pytest.raises(VariableValueError):
        validate_value("v", vtype, raw)


@pytest.mark.parametrize(
    ("vtype", "raw", "expected"),
    [
        (VariableType.TEXT_LIST, ["EMEA"], ["EMEA"]),
        (VariableType.TEXT, "paid", "paid"),
        (VariableType.NUMBER, 0.5, Decimal("0.5")),
        (VariableType.BOOLEAN, False, False),
        (VariableType.DATE, "2026-07-01", dt.date(2026, 7, 1)),
    ],
)
def test_accepts_valid_values(vtype, raw, expected):
    assert validate_value("v", vtype, raw) == expected


# ── numbers (finite, exact) ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw", [float("inf"), float("-inf"), float("nan"), Decimal("NaN"), Decimal("Infinity")]
)
def test_number_rejects_non_finite(raw):
    with pytest.raises(VariableValueError, match="NaN and Infinity"):
        validate_value("n", VariableType.NUMBER, raw)


@pytest.mark.parametrize("raw", ["1000.50", "5", None, [1], False])
def test_number_input_is_a_json_number_only(raw):
    with pytest.raises(VariableValueError, match="a JSON number"):
        validate_value("n", VariableType.NUMBER, raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(1000.5, Decimal("1000.5")), (7, Decimal(7)), (Decimal("1000.50"), Decimal("1000.50"))],
)
def test_number_is_validated_into_exact_decimal(raw, expected):
    value = validate_value("n", VariableType.NUMBER, raw)
    assert isinstance(value, Decimal) and str(value) == str(expected)


def test_number_to_plain():
    assert to_plain(VariableType.NUMBER, Decimal("1E+3")) == 1000
    assert isinstance(to_plain(VariableType.NUMBER, Decimal("1E+3")), int)
    assert to_plain(VariableType.NUMBER, Decimal("1000.50")) == 1000.5


def test_placeholder_names_per_place():
    assert placeholder_name("period", "start") == "period__start"
    assert placeholder_name("period", "start", 2) == "period__2__start"
    assert placeholder_name("regions") == "regions"
    assert placeholder_name("regions", "", 3) == "regions__3"


def test_read_and_write_date_with_format():
    assert read_date("03-04-2026", "%m-%d-%Y") == dt.date(2026, 3, 4)
    assert write_date(dt.date(2026, 3, 4), "%m-%d-%Y") == "03-04-2026"
    assert read_date("2026-03-04", None) == dt.date(2026, 3, 4)
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        read_date("2026-03-04 00:00:00", None)
    with pytest.raises(ValueError, match="time of day"):
        read_date("03-04-2026 10:30", "%m-%d-%Y %H:%M")
