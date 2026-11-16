"""Column-side and value-side wrappers (ADR 0014, 0015)."""

import pytest

from cteforge.domain.dialects import Dialect
from cteforge.domain.models import LiteralForm
from cteforge.domain.variables import VariableType
from cteforge.parsing.analyze import LiteralContext as C
from cteforge.parsing.analyze import analyze
from cteforge.parsing.types import compatible_variable_types, data_type

D = Dialect.DUCKDB
SCHEMA = {
    "deals": {"closed_at": "TIMESTAMP", "region": "VARCHAR", "amount": "DECIMAL(12,2)"},
    "gpus": {"compute_flops": "DOUBLE", "gpu_model": "VARCHAR"},
}


def _one(sql: str, dialect: Dialect = D):
    literals = analyze(sql, dialect, SCHEMA).literals
    assert len(literals) == 1, literals
    return literals[0]


# ── Column side ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "column_side",
    ["CAST(closed_at AS DATE)", "closed_at::DATE", "DATE(closed_at)"],
)
def test_date_casts_on_column_side(column_side):
    lit = _one(f"SELECT * FROM deals WHERE {column_side} >= '2026-01-01'")
    assert lit.value == "2026-01-01" and lit.operator == ">="
    assert (lit.column.table, lit.column.column, lit.column.type) == (
        "deals",
        "closed_at",
        "TIMESTAMP",
    )
    assert lit.wrapped
    assert lit.compared_type == "DATE"
    assert lit.context == []


def test_extract_is_integer_typed():
    lit = _one("SELECT * FROM deals WHERE EXTRACT(YEAR FROM closed_at) = 2025")
    assert lit.value == "2025" and lit.column.column == "closed_at" and lit.wrapped
    dtype = data_type(lit.compared_type, D)
    assert dtype is not None and dtype.this in dtype.INTEGER_TYPES


def test_lower_is_text_typed():
    lit = _one("SELECT * FROM deals WHERE LOWER(region) = 'emea'")
    assert lit.column.column == "region" and lit.column.type == "VARCHAR" and lit.wrapped
    assert compatible_variable_types(lit.compared_type, D) == {
        VariableType.TEXT,
        VariableType.TEXT_LIST,
    }


def test_type_preserving_wrapper_falls_back_to_column_type():
    lit = _one("SELECT * FROM deals WHERE DATE_TRUNC('month', closed_at) = '2026-01-01'")
    assert lit.compared_type == "TIMESTAMP"
    lit = _one("SELECT * FROM deals WHERE amount * 2 > 100")
    assert lit.compared_type == "DECIMAL(12, 2)"  # sqlglot spelling for expressions


def test_unwrapped_compared_type_is_column_type():
    lit = _one("SELECT * FROM deals WHERE amount > 100")
    assert not lit.wrapped and lit.compared_type == "DECIMAL(12,2)"


def test_derived_cte_column_is_typed_from_its_expression():
    sql = """
    WITH m AS (SELECT CAST(closed_at AS DATE) AS day FROM deals)
    SELECT * FROM m WHERE day >= '2026-01-01'
    """
    lit = _one(sql)
    assert lit.column.derived and lit.column.type is None
    assert lit.compared_type == "DATE"


def test_column_side_function_literals_are_not_ids():
    sql = (
        "SELECT * FROM deals"
        " WHERE ROUND(amount, 2) > 100 AND DATE_TRUNC('month', closed_at) = '2026-01-01'"
    )
    assert [lit.value for lit in analyze(sql, D, SCHEMA).literals] == ["100", "2026-01-01"]


def test_constant_comparisons_are_ignored():
    assert analyze("SELECT * FROM deals WHERE 1 = 1 AND 'a' || 'b' = 'ab'", D).literals == []


# ── Value side ──────────────────────────────────────────────────────────────────────


def test_arithmetic_on_value_side():
    a = analyze("SELECT * FROM gpus WHERE compute_flops >= 500 * 1e12", D, SCHEMA)
    assert [(lit.value, lit.wrapper_sql, lit.context) for lit in a.literals] == [
        ("500", "? * 1e12", [C.VALUE_WRAPPED]),
        ("1e12", "500 * ?", [C.VALUE_WRAPPED]),
    ]
    assert a.literals[0].compared_type == "DOUBLE"
    assert a.literals[0].operator == ">="


def test_concatenation_on_value_side():
    a = analyze("SELECT * FROM gpus WHERE gpu_model LIKE '%' || 'H100' || '%'", D, SCHEMA)
    h100 = next(lit for lit in a.literals if lit.value == "H100")
    assert h100.wrapper_sql == "'%' || ? || '%'"
    assert h100.operator == "LIKE" and h100.context == [C.VALUE_WRAPPED]
    assert len(a.literals) == 3  # every literal in a filter position gets an ID


def test_function_in_in_list_and_between():
    a = analyze(
        "SELECT * FROM deals WHERE region IN (UPPER('emea'), 'NA')"
        " AND amount BETWEEN ABS(-5) AND 10",
        D,
        SCHEMA,
    )
    info = {lit.value: (lit.operator, lit.wrapper_sql, lit.in_list) for lit in a.literals}
    assert info == {
        "emea": ("IN", "UPPER(?)", "main.IN1"),
        "NA": ("IN", None, "main.IN1"),
        "-5": ("BETWEEN_LOW", "ABS(?)", None),
        "10": ("BETWEEN_HIGH", None, None),
    }


def test_direct_cast_stays_typed_not_wrapped():
    lit = _one("SELECT * FROM deals WHERE closed_at >= CAST('2026-01-01' AS TIMESTAMP)")
    assert lit.form is LiteralForm.TYPED and lit.cast_type == "TIMESTAMP"
    assert lit.wrapper_sql is None and lit.context == []


def test_cast_inside_wrapper_is_typed_and_wrapped():
    a = analyze(
        "SELECT * FROM deals WHERE closed_at >= CAST('2026-01-01' AS DATE) - INTERVAL '30' DAY",
        D,
        SCHEMA,
    )
    first = a.literals[0]
    assert first.form is LiteralForm.TYPED
    assert first.wrapper_sql == "? - INTERVAL '30' DAY"
    assert first.context == [C.VALUE_WRAPPED]


def test_parenthesized_value_is_not_wrapped():
    lit = _one("SELECT * FROM deals WHERE amount > (100)")
    assert lit.context == [] and lit.wrapper_sql is None


# ── Format arguments ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("dialect", "value_side", "wrapper"),
    [
        (Dialect.DUCKDB, "strptime('01-01-2026', '%m-%d-%Y')", "STRPTIME(?, '%m-%d-%Y')"),
        (Dialect.SNOWFLAKE, "TO_DATE('01-01-2026', 'MM-DD-YYYY')", None),
        (Dialect.SNOWFLAKE, "TO_TIMESTAMP('01-01-2026', 'MM-DD-YYYY')", None),
        (Dialect.POSTGRES, "TO_DATE('01-01-2026', 'MM-DD-YYYY')", "TO_DATE(?, 'MM-DD-YYYY')"),
        (Dialect.BIGQUERY, "PARSE_DATE('%m-%d-%Y', '01-01-2026')", "PARSE_DATE('%m-%d-%Y', ?)"),
    ],
)
def test_format_argument_suggests_strftime_and_gets_no_id(dialect, value_side, wrapper):
    lit = _one(f"SELECT * FROM deals WHERE closed_at >= {value_side}", dialect)
    assert lit.value == "01-01-2026"
    assert lit.suggested_format == "%m-%d-%Y"
    assert lit.context == [C.VALUE_WRAPPED]
    assert lit.wrapper_sql is not None and "?" in lit.wrapper_sql
    if wrapper is not None:
        assert lit.wrapper_sql == wrapper


def test_format_that_cannot_read_the_value_is_not_suggested():
    lit = _one("SELECT * FROM deals WHERE closed_at >= strptime('2026/01/01', '%m-%d-%Y')")
    assert lit.suggested_format is None


def test_no_format_without_parse_function():
    lit = _one("SELECT * FROM deals WHERE closed_at >= '01-01-2026'")
    assert lit.suggested_format is None and lit.wrapper_sql is None
