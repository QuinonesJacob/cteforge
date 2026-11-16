"""Grain candidates (M1.5): every date truncation gets a `<scope>.G<n>` ID with facts."""

import pytest

from cteforge.domain.dialects import Dialect
from cteforge.parsing.analyze import LiteralContext, analyze

SCHEMA = {"orders": {"id": "INTEGER", "created_at": "TIMESTAMP", "day": "DATE"}}

# One truncation of `created_at` to month, written natively per dialect.
MONTHLY = {
    Dialect.DUCKDB: "SELECT DATE_TRUNC('month', o.created_at) AS m FROM orders AS o",
    Dialect.POSTGRES: "SELECT date_trunc('month', o.created_at) AS m FROM orders AS o",
    Dialect.SNOWFLAKE: "SELECT DATE_TRUNC(month, o.created_at) AS m FROM orders AS o",
    Dialect.BIGQUERY: "SELECT DATETIME_TRUNC(o.created_at, MONTH) AS m FROM orders AS o",
    Dialect.DATABRICKS: "SELECT DATE_TRUNC('MONTH', o.created_at) AS m FROM orders AS o",
}


@pytest.mark.parametrize("dialect", list(Dialect))
def test_truncation_is_a_grain_candidate_in_every_dialect(dialect):
    analysis = analyze(MONTHLY[dialect], dialect, SCHEMA)
    assert [g.id for g in analysis.grains] == ["main.G1"]
    grain = analysis.grain("main.G1")
    assert grain.unit == "month"
    assert grain.scope == "main"
    assert grain.column_sql == "o.created_at"
    assert grain.column.table == "orders" and grain.column.column == "created_at"
    assert grain.column_type == "TIMESTAMP"
    assert grain.output == "m"
    assert grain.context == []
    assert analysis.literals == []  # The unit argument is never a filter literal.


def test_ids_count_per_scope_in_walk_order():
    sql = """
    WITH a AS (
      SELECT DATE_TRUNC('day', created_at) AS d, DATE_TRUNC('year', created_at) AS y FROM orders
    )
    SELECT DATE_TRUNC('week', d) AS w FROM a
    """
    analysis = analyze(sql, Dialect.DUCKDB, SCHEMA)
    assert [(g.id, g.unit, g.output) for g in analysis.grains] == [
        ("a.G1", "day", "d"),
        ("a.G2", "year", "y"),
        ("main.G1", "week", "w"),
    ]
    # Lineage stops at the computed CTE column; its type comes from the computing expression.
    outer = analysis.grain("main.G1")
    assert (outer.column.table, outer.column.column, outer.column.derived) == ("a", "d", True)
    assert outer.column_type == "TIMESTAMP"
    assert analysis.grain("main.G9") is None


@pytest.mark.parametrize(
    ("dialect", "sql", "unit"),
    [
        (Dialect.DATABRICKS, "SELECT TRUNC(day, 'MM') FROM orders", "month"),
        (Dialect.SNOWFLAKE, "SELECT DATE_TRUNC('Q', created_at) FROM orders", "quarter"),
        (Dialect.SNOWFLAKE, "SELECT DATE_TRUNC('yyyy', created_at) FROM orders", "year"),
        (Dialect.DUCKDB, "SELECT DATE_TRUNC('hour', created_at) FROM orders", "hour"),
        (Dialect.BIGQUERY, "SELECT DATE_TRUNC(day, WEEK) FROM orders", "week"),
        (Dialect.BIGQUERY, "SELECT DATE_TRUNC(day, WEEK(SUNDAY)) FROM orders", "week"),
        (Dialect.BIGQUERY, "SELECT DATE_TRUNC(day, WEEK(MONDAY)) FROM orders", "week(monday)"),
        (Dialect.BIGQUERY, "SELECT DATE_TRUNC(day, ISOWEEK) FROM orders", "isoweek"),
        (Dialect.BIGQUERY, "SELECT TIMESTAMP_TRUNC(created_at, QUARTER) FROM orders", "quarter"),
    ],
)
def test_units_are_normalized_lower_case(dialect, sql, unit):
    (grain,) = analyze(sql, dialect, SCHEMA).grains
    assert grain.unit == unit


def test_truncations_in_filters_carry_context_and_keep_their_literals():
    sql = """
    SELECT id FROM orders
    WHERE DATE_TRUNC('month', created_at) = DATE_TRUNC('month', TIMESTAMP '2026-03-05')
       OR id = 1
    """
    analysis = analyze(sql, Dialect.DUCKDB, SCHEMA)
    assert [g.id for g in analysis.grains] == ["main.G1", "main.G2"]
    assert all(g.context == [LiteralContext.OR_BRANCH] for g in analysis.grains)
    assert all(g.output is None for g in analysis.grains)
    # The timestamp inside the value-side truncation is still a filter literal.
    assert [(lit.id, lit.value) for lit in analysis.literals] == [
        ("main.L1", "2026-03-05"),
        ("main.L2", "1"),
    ]


def test_nested_truncations_each_get_an_id():
    sql = "SELECT DATE_TRUNC('month', DATE_TRUNC('day', created_at)) AS m FROM orders"
    analysis = analyze(sql, Dialect.DUCKDB, SCHEMA)
    assert [(g.id, g.unit) for g in analysis.grains] == [("main.G1", "month"), ("main.G2", "day")]
    assert analysis.grain("main.G1").output == "m"
    assert analysis.grain("main.G2").output is None


def test_having_and_case_facts():
    sql = """
    SELECT COUNT(*) FROM orders
    GROUP BY id
    HAVING MAX(DATE_TRUNC('day', created_at)) > TIMESTAMP '2026-01-01'
    """
    (grain,) = analyze(sql, Dialect.DUCKDB, SCHEMA).grains
    assert grain.context == [LiteralContext.HAVING]


def test_no_truncation_means_no_grains(sample_sql, sample_schema):
    assert analyze("SELECT id FROM orders", Dialect.DUCKDB, SCHEMA).grains == []
    (grain,) = analyze(sample_sql, Dialect.DUCKDB, sample_schema).grains
    assert (grain.id, grain.unit, grain.output) == ("deals_in_region.G1", "month", "month")
    assert grain.column.table == "deals" and grain.column_type == "TIMESTAMP"


# ── Units read from a column (the value decides the unit at run time) ───────────────


@pytest.mark.parametrize(
    ("dialect", "sql", "unit"),
    [
        # sqlglot keeps a qualified name as a Column.
        (
            Dialect.DUCKDB,
            "SELECT DATE_TRUNC(cfg.month, o.created_at) FROM orders AS o, cfg",
            "cfg.month",
        ),
        (
            Dialect.POSTGRES,
            "SELECT date_trunc(cfg.grain, o.created_at) FROM orders AS o, cfg",
            "cfg.grain",
        ),
        # A bare or double-quoted name is a column where the unit is a string expression,
        # although sqlglot reads it as the unit keyword.
        (Dialect.DUCKDB, "SELECT DATE_TRUNC(month, o.created_at) FROM orders AS o, cfg", "month"),
        (Dialect.DUCKDB, 'SELECT DATE_TRUNC("q", o.created_at) FROM orders AS o, cfg', "q"),
        (Dialect.POSTGRES, "SELECT date_trunc(grain, o.created_at) FROM orders AS o, cfg", "grain"),
        (Dialect.DATABRICKS, "SELECT DATE_TRUNC(month, o.created_at) FROM orders AS o", "month"),
        (Dialect.DATABRICKS, "SELECT TRUNC(o.day, fmt) FROM orders AS o", "fmt"),
    ],
)
def test_a_unit_read_from_a_column_is_reported_as_that_column(dialect, sql, unit):
    (grain,) = analyze(sql, dialect, SCHEMA).grains
    assert grain.unit == f"column {unit}"


@pytest.mark.parametrize(
    ("dialect", "sql"),
    [
        # Snowflake and BigQuery take unit keywords: a bare name there is the unit.
        (Dialect.SNOWFLAKE, "SELECT DATE_TRUNC(month, created_at) FROM orders"),
        (Dialect.BIGQUERY, "SELECT DATE_TRUNC(day, MONTH) FROM orders"),
        (Dialect.DUCKDB, "SELECT DATE_TRUNC('month', created_at) FROM orders"),
        (Dialect.DATABRICKS, "SELECT TRUNC(day, 'MM') FROM orders"),
        # A column named like the unit elsewhere in the query does not matter.
        (Dialect.DUCKDB, "SELECT month, DATE_TRUNC('month', created_at) FROM orders"),
    ],
)
def test_unit_keywords_and_strings_stay_units(dialect, sql):
    (grain,) = analyze(sql, dialect, SCHEMA).grains
    assert grain.unit == "month"


def test_a_name_used_as_a_column_unit_marks_every_truncation_to_it():
    """Conservative: the tokens tell a bare column from a string, the parsed tree does not,
    so a query that uses both for the same unit reports both as the column."""
    sql = (
        "SELECT DATE_TRUNC('month', o.created_at), DATE_TRUNC(month, o.created_at) "
        "FROM orders AS o, cfg"
    )
    assert [g.unit for g in analyze(sql, Dialect.DUCKDB, SCHEMA).grains] == [
        "column month",
        "column month",
    ]
