import pytest

from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import SqlParseError
from cteforge.domain.models import LiteralForm
from cteforge.domain.variables import RangeShape, VariableType
from cteforge.parsing.analyze import analyze, referenced_tables
from cteforge.parsing.types import compatible_variable_types

D = Dialect.DUCKDB


@pytest.fixture(scope="module")
def analysis(sample_sql, sample_schema):
    return analyze(sample_sql, D, sample_schema)


def test_structure(analysis):
    assert analysis.tables == ["accounts", "deals", "invoices"]
    assert [c.name for c in analysis.ctes] == [
        "closed_deals",
        "deals_in_region",
        "collected",
    ]
    assert analysis.outputs == ["month", "region", "realization_rate"]


def test_literal_ids_and_values(analysis):
    assert [(lit.id, lit.value) for lit in analysis.literals] == [
        ("closed_deals.L1", "2026-01-01"),
        ("closed_deals.L2", "2026-04-01"),
        ("closed_deals.L3", "closed_won"),
        ("closed_deals.L4", "closed_won_renewal"),
        ("deals_in_region.L1", "EMEA"),
        ("deals_in_region.L2", "NA"),
        ("collected.L1", "paid"),
    ]


def test_typed_timestamp_literal_and_column_type(analysis):
    start = analysis.literal("closed_deals.L1")
    assert start.form is LiteralForm.TYPED
    assert start.cast_type == "TIMESTAMP"
    assert start.operator == ">="
    assert (start.column.table, start.column.column, start.column.type) == (
        "deals",
        "closed_at",
        "TIMESTAMP",
    )


def test_in_list_grouping(analysis):
    groups = {lit.id: lit.in_list for lit in analysis.literals if lit.in_list}
    assert groups["closed_deals.L3"] == groups["closed_deals.L4"]
    assert groups["deals_in_region.L1"] == groups["deals_in_region.L2"]
    assert groups["closed_deals.L3"] != groups["deals_in_region.L1"]


def test_alias_resolves_to_base_table(analysis):
    region = analysis.literal("deals_in_region.L1")
    assert (region.column.table, region.column.column, region.column.type) == (
        "accounts",
        "region",
        "VARCHAR",
    )


def test_half_open_range_detected(analysis):
    assert len(analysis.ranges) == 1
    r = analysis.ranges[0]
    assert (r.start, r.end, r.shape) == (
        "closed_deals.L1",
        "closed_deals.L2",
        RangeShape.HALF_OPEN,
    )


def test_between_is_inclusive_range():
    a = analyze("SELECT * FROM deals WHERE closed_at BETWEEN '2026-01-01' AND '2026-03-31'", D)
    assert [(r.start, r.end, r.shape) for r in a.ranges] == [
        ("main.L1", "main.L2", RangeShape.INCLUSIVE)
    ]
    assert a.literals[0].form is LiteralForm.BARE


def test_lineage_through_cte(sample_schema):
    sql = """
    WITH cd AS (SELECT d.closed_at AS closed, d.amount FROM deals AS d)
    SELECT * FROM cd WHERE cd.closed >= '2026-01-01'
    """
    lit = analyze(sql, D, sample_schema).literals[0]
    assert (lit.column.table, lit.column.column, lit.column.type) == (
        "deals",
        "closed_at",
        "TIMESTAMP",
    )


def test_derived_cte_column_is_flagged(sample_schema):
    sql = """
    WITH m AS (SELECT DATE_TRUNC('month', closed_at) AS month FROM deals)
    SELECT * FROM m WHERE month >= '2026-01-01'
    """
    lit = analyze(sql, D, sample_schema).literals[0]
    assert lit.column.derived and lit.column.table == "m"


def test_wrapped_column(sample_schema):
    lit = analyze("SELECT * FROM accounts WHERE LOWER(region) = 'emea'", D, sample_schema).literals[
        0
    ]
    assert lit.wrapped
    assert lit.column_sql == "LOWER(region)"
    assert lit.column.column == "region" and lit.column.type == "VARCHAR"


def test_literal_on_left_flips_operator():
    lit = analyze("SELECT * FROM deals WHERE 100 < amount", D).literals[0]
    assert lit.operator == ">"


def test_non_filter_literals_are_ignored():
    a = analyze("SELECT ROUND(amount, 2), COALESCE(amount, 0) FROM deals LIMIT 5", D)
    assert a.literals == []


def test_referenced_tables_excludes_ctes(sample_sql):
    assert referenced_tables(sample_sql, D) == ["accounts", "deals", "invoices"]


@pytest.mark.parametrize("sql", ["SELEC 1", "SELECT 1; SELECT 2"])
def test_parse_errors(sql):
    with pytest.raises(SqlParseError):
        analyze(sql, D)


@pytest.mark.parametrize(
    ("type_str", "expected"),
    [
        ("TIMESTAMP", {VariableType.DATE, VariableType.DATE_RANGE}),
        ("DATE", {VariableType.DATE, VariableType.DATE_RANGE}),
        ("VARCHAR", {VariableType.TEXT, VariableType.TEXT_LIST}),
        ("DECIMAL(12,2)", {VariableType.NUMBER}),
        ("BOOLEAN", {VariableType.BOOLEAN}),
        (None, set()),
    ],
)
def test_compatible_variable_types(type_str, expected):
    assert compatible_variable_types(type_str, D) == expected
