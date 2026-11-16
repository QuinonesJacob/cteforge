import datetime as dt

import pytest

from cteforge.domain.errors import VariableValueError
from cteforge.domain.variables import DateRangeValue, VariableType, validate_value


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
        (VariableType.NUMBER, 0.5, 0.5),
        (VariableType.BOOLEAN, False, False),
        (VariableType.DATE, "2026-07-01", dt.date(2026, 7, 1)),
    ],
)
def test_accepts_valid_values(vtype, raw, expected):
    assert validate_value("v", vtype, raw) == expected
