"""Regression tests: positional CTE visibility, memoized derived types, date-part units,
list / row filter shapes, and semi / anti joins."""

import time

import pytest

from cteforge.domain.dialects import Dialect
from cteforge.parsing.analyze import LiteralContext as C
from cteforge.parsing.analyze import analyze, referenced_tables

D = Dialect.DUCKDB
SCHEMA = {
    "deals": {
        "amount": "DECIMAL(12,2)",
        "fee": "DECIMAL(12,2)",
        "region": "VARCHAR",
        "close_date": "DATE",
        "acct": "INTEGER",
        "a": "INTEGER",
        "b": "INTEGER",
    },
    "accounts": {"id": "INTEGER", "tier": "VARCHAR"},
}


def _literals(sql: str, dialect: Dialect = D):
    return analyze(sql, dialect, SCHEMA).literals


# ── CTE visibility is positional ────────────────────────────────────────────────────


def test_cte_body_reading_its_own_name_reads_the_base_table():
    sql = (
        "WITH deals AS (SELECT * FROM deals WHERE region = 'EMEA') "
        "SELECT * FROM deals WHERE amount > 5"
    )
    analysis = analyze(sql, D, SCHEMA)
    assert analysis.tables == ["deals"]
    assert referenced_tables(sql, D) == ["deals"]
    assert analysis.ctes[0].reads == ["deals"]
    inner, outer = analysis.literals
    assert (inner.id, inner.column.table, inner.column.type) == ("deals.L1", "deals", "VARCHAR")
    assert outer.column.type == "DECIMAL(12,2)" and not outer.column.derived


def test_earlier_cte_does_not_see_a_later_cte_of_the_same_name():
    sql = (
        "WITH a AS (SELECT * FROM deals WHERE amount > 1), deals AS (SELECT * FROM a) "
        "SELECT * FROM deals"
    )
    analysis = analyze(sql, D, SCHEMA)
    assert analysis.tables == ["deals"]
    assert [c.reads for c in analysis.ctes] == [["deals"], ["a"]]
    lit = analysis.literals[0]
    assert (lit.column.table, lit.column.type, lit.compared_type) == (
        "deals",
        "DECIMAL(12,2)",
        "DECIMAL(12,2)",
    )


def test_self_named_derived_cte_is_typed_and_fast():
    sql = (
        "WITH deals AS (SELECT amount + fee + amount AS amount FROM deals) "
        "SELECT * FROM deals WHERE amount * 2 > 5"
    )
    start = time.perf_counter()
    lit = _literals(sql)[0]
    assert time.perf_counter() - start < 0.5
    assert lit.column.derived and lit.compared_type == "DECIMAL(12, 2)"


def test_recursive_cte_still_sees_itself():
    sql = (
        "WITH RECURSIVE t AS (SELECT 1 AS n UNION ALL SELECT n + 1 FROM t WHERE n < 5) "
        "SELECT * FROM t WHERE n > 2"
    )
    analysis = analyze(sql, Dialect.POSTGRES, SCHEMA)
    assert analysis.tables == []
    assert [lit.column.table for lit in analysis.literals] == ["t", "t"]


# ── Derived types are memoized ──────────────────────────────────────────────────────


@pytest.mark.parametrize("layers", [12, 30])
def test_layered_cte_chain_types_in_linear_time(layers):
    ctes = ["c0 AS (SELECT amount AS x FROM deals)"]
    ctes += [f"c{i} AS (SELECT x + x + x AS x FROM c{i - 1})" for i in range(1, layers)]
    sql = f"WITH {', '.join(ctes)} SELECT * FROM c{layers - 1} WHERE x * 2 > 5"
    start = time.perf_counter()
    lit = _literals(sql)[0]
    assert time.perf_counter() - start < 1.0  # was ~28 s at 11 layers before memoization
    assert lit.compared_type == "DECIMAL(12, 2)"


# ── Date-part units are not values ──────────────────────────────────────────────────


def test_date_trunc_unit_on_value_side_is_not_a_literal():
    lits = _literals(
        "SELECT * FROM deals WHERE close_date >= DATE_TRUNC('month', DATE '2026-01-01')"
    )
    assert len(lits) == 1
    lit = lits[0]
    assert (lit.id, lit.value, lit.compared_type) == ("main.L1", "2026-01-01", "DATE")
    assert lit.wrapper_sql == "DATE_TRUNC('MONTH', ?)"


@pytest.mark.parametrize("func", ["DATE_PART", "DATEPART"])
def test_unparsed_date_part_unit_is_not_a_literal(func):
    sql = f"SELECT * FROM deals WHERE EXTRACT(year FROM close_date) = {func}('year', CURRENT_DATE)"
    assert _literals(sql) == []


def test_date_part_unit_does_not_shift_later_ids():
    lits = _literals(
        "SELECT * FROM deals WHERE EXTRACT(year FROM close_date) = DATE_PART('year', "
        "DATE '2026-03-01') AND region = 'EMEA'"
    )
    assert [(lit.id, lit.value) for lit in lits] == [("main.L1", "2026-03-01"), ("main.L2", "EMEA")]


# ── List and row shapes ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("dialect", "where", "operator", "values"),
    [
        (Dialect.POSTGRES, "region = ANY(ARRAY['a', 'b'])", "= ANY", ["a", "b"]),
        (Dialect.POSTGRES, "region <> ALL(ARRAY['a', 'b'])", "<> ALL", ["a", "b"]),
        (Dialect.BIGQUERY, "region IN UNNEST(['a', 'b'])", "IN UNNEST", ["a", "b"]),
        (Dialect.SNOWFLAKE, "region ILIKE ANY ('a%', 'b%')", "ILIKE ANY", ["a%", "b%"]),
        (Dialect.POSTGRES, "region IS DISTINCT FROM 'a'", "IS DISTINCT FROM", ["a"]),
        (Dialect.POSTGRES, "region IS NOT DISTINCT FROM 'a'", "IS NOT DISTINCT FROM", ["a"]),
        (Dialect.POSTGRES, "region SIMILAR TO 'a%'", "SIMILAR TO", ["a%"]),
    ],
)
def test_list_and_null_safe_shapes_get_ids(dialect, where, operator, values):
    lits = _literals(f"SELECT * FROM deals WHERE {where}", dialect)
    assert [lit.value for lit in lits] == values
    for lit in lits:
        assert lit.operator == operator
        assert (lit.column.column, lit.compared_type) == ("region", "VARCHAR")
        assert lit.in_list is None and lit.wrapper_sql is None
        # Excluding operators keep everything BUT the value: reported like a NOT.
        excluding = operator in ("<> ALL", "IS DISTINCT FROM")
        assert lit.context == ([C.NEGATED] if excluding else [])


@pytest.mark.parametrize(
    ("where", "negated"),
    [
        ("status <> 'void'", True),
        ("status != 'void'", True),
        ("'void' <> status", True),
        ("status = 'void'", False),
        ("NOT status = 'void'", True),
        ("status IS NOT DISTINCT FROM 'void'", False),
    ],
)
def test_excluding_comparisons_are_negated(where: str, negated: bool) -> None:
    (lit,) = _literals(f"SELECT * FROM invoices WHERE {where}", Dialect.DUCKDB)
    assert (C.NEGATED in lit.context) is negated


def test_row_comparison_maps_each_element_to_its_column():
    lits = _literals("SELECT * FROM deals WHERE (a, b) = (1, 2)")
    assert [(lit.operator, lit.value, lit.column.column) for lit in lits] == [
        ("ROW =", "1", "a"),
        ("ROW =", "2", "b"),
    ]


def test_row_in_list_maps_elements_and_forms_no_in_group():
    lits = _literals("SELECT * FROM deals WHERE (a, b) IN ((1, 2), (3, 4))")
    assert [(lit.operator, lit.value, lit.column.column) for lit in lits] == [
        ("ROW IN", "1", "a"),
        ("ROW IN", "2", "b"),
        ("ROW IN", "3", "a"),
        ("ROW IN", "4", "b"),
    ]
    assert all(lit.in_list is None for lit in lits)


def test_any_over_subquery_is_not_a_list():
    lits = _literals(
        "SELECT * FROM deals WHERE region = ANY(SELECT tier FROM accounts WHERE id > 3)",
        Dialect.POSTGRES,
    )
    assert [(lit.operator, lit.column.column) for lit in lits] == [(">", "id")]


def test_plain_in_list_still_groups():
    lits = _literals("SELECT * FROM deals WHERE region IN ('a', 'b')")
    assert [(lit.operator, lit.in_list) for lit in lits] == [("IN", "main.IN1")] * 2


# ── Semi and anti joins ─────────────────────────────────────────────────────────────

_JOIN = "SELECT * FROM deals d LEFT {kind} JOIN accounts a ON a.id = d.acct AND a.tier = 'g'"


def test_left_semi_join_is_semi_and_filters_like_inner():
    analysis = analyze(_JOIN.format(kind="SEMI"), Dialect.DATABRICKS, SCHEMA)
    join = analysis.joins[0]
    assert (join.left_table, join.right_table, join.join_type, join.keys) == (
        "deals",
        "accounts",
        "semi",
        [("acct", "id")],
    )
    assert analysis.literals[0].context == [C.JOIN_ON]


def test_left_anti_join_is_anti_and_negated():
    analysis = analyze(_JOIN.format(kind="ANTI"), Dialect.DATABRICKS, SCHEMA)
    assert analysis.joins[0].join_type == "anti"
    assert analysis.literals[0].context == [C.NEGATED, C.JOIN_ON]


def test_left_outer_join_is_still_outer():
    analysis = analyze(_JOIN.format(kind="OUTER"), Dialect.DATABRICKS, SCHEMA)
    assert analysis.joins[0].join_type == "left"
    assert analysis.literals[0].context == [C.OUTER_JOIN_ON]
