"""Structural context facts per literal and range-candidate pairing (ADR 0015)."""

import pytest

from cteforge.domain.dialects import Dialect
from cteforge.domain.variables import RangeShape
from cteforge.parsing.analyze import LiteralContext as C
from cteforge.parsing.analyze import analyze

D = Dialect.DUCKDB
SCHEMA = {"deals": {"closed_at": "TIMESTAMP", "region": "VARCHAR", "amount": "DECIMAL(12,2)"}}


def _contexts(sql: str, dialect: Dialect = D) -> dict[str, list[C]]:
    return {lit.value: lit.context for lit in analyze(sql, dialect, SCHEMA).literals}


def _ranges(sql: str) -> list[tuple[str, str, RangeShape]]:
    return [(r.start, r.end, r.shape) for r in analyze(sql, D, SCHEMA).ranges]


# ── Context facts ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "where",
    [
        "NOT (amount = 5)",
        "amount NOT BETWEEN 5 AND 6",
        "amount NOT IN (5, 6)",
        "region NOT LIKE '5%'",
        "NOT region ILIKE '5%'",
    ],
)
def test_negated_forms(where):
    contexts = _contexts(f"SELECT * FROM deals WHERE {where}")
    assert contexts and all(ctx == [C.NEGATED] for ctx in contexts.values())


def test_plain_where_has_no_context():
    assert _contexts("SELECT * FROM deals WHERE amount = 5 AND region = 'EMEA'") == {
        "5": [],
        "EMEA": [],
    }


def test_or_branch():
    contexts = _contexts(
        "SELECT * FROM deals WHERE closed_at >= '2026-01-01' OR closed_at < '2025-01-01'"
    )
    assert contexts == {"2026-01-01": [C.OR_BRANCH], "2025-01-01": [C.OR_BRANCH]}


def test_case_condition_and_filter_clause():
    contexts = _contexts(
        "SELECT SUM(CASE WHEN amount > 100 THEN 1 END), COUNT(*) FILTER (WHERE amount < 7),"
        " IF(region = 'NA', 1, 0) FROM deals"
    )
    assert contexts == {
        "100": [C.CASE_CONDITION],
        "7": [C.CASE_CONDITION],
        "NA": [C.CASE_CONDITION],
    }


def test_join_on_inner_vs_outer():
    sql = """
    SELECT * FROM deals d
    JOIN deals e ON e.region = d.region AND e.amount = 3
    LEFT JOIN deals f ON f.region = d.region AND f.amount = 4
    FULL OUTER JOIN deals g ON g.region = d.region AND g.amount = 5
    """
    assert _contexts(sql) == {
        "3": [C.JOIN_ON],
        "4": [C.OUTER_JOIN_ON],
        "5": [C.OUTER_JOIN_ON],
    }


def test_having_and_subqueries():
    sql = """
    SELECT region, COUNT(*) FROM deals
    WHERE region IN (SELECT region FROM deals WHERE amount = 7)
      AND EXISTS (SELECT 1 FROM deals WHERE amount = 8)
      AND amount > (SELECT MIN(amount) FROM deals WHERE region = 'X')
    GROUP BY region HAVING COUNT(*) > 2
    """
    assert _contexts(sql) == {
        "7": [C.SUBQUERY],
        "8": [C.SUBQUERY],
        "X": [C.SUBQUERY],
        "2": [C.HAVING],
    }


def test_derived_table_is_subquery():
    contexts = _contexts("SELECT * FROM (SELECT * FROM deals WHERE amount = 9) AS t")
    assert contexts == {"9": [C.SUBQUERY]}


def test_multiple_facts_are_ordered():
    contexts = _contexts(
        "SELECT * FROM deals WHERE NOT (amount = 1 OR region = 'A') AND region IS NOT NULL"
    )
    assert contexts == {"1": [C.NEGATED, C.OR_BRANCH], "A": [C.NEGATED, C.OR_BRANCH]}


def test_cte_literal_context_stops_at_cte_scope():
    sql = "WITH x AS (SELECT * FROM deals WHERE amount = 1) SELECT * FROM x"
    assert _contexts(sql) == {"1": []}


# ── Range candidates ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("low", "high", "shape"),
    [
        (">=", "<", RangeShape.HALF_OPEN),
        (">=", "<=", RangeShape.INCLUSIVE),
        (">", "<", RangeShape.EXCLUSIVE),
        (">", "<=", RangeShape.START_EXCLUSIVE),
    ],
)
def test_shapes_from_operators(low, high, shape):
    sql = (
        f"SELECT * FROM deals WHERE closed_at {low} '2026-01-01' AND closed_at {high} '2026-04-01'"
    )
    assert _ranges(sql) == [("main.L1", "main.L2", shape)]


def test_literal_on_left_is_flipped_before_pairing():
    sql = "SELECT * FROM deals WHERE '2026-01-01' < closed_at AND closed_at <= '2026-04-01'"
    assert _ranges(sql) == [("main.L1", "main.L2", RangeShape.START_EXCLUSIVE)]


def test_or_is_not_a_range():
    sql = "SELECT * FROM deals WHERE closed_at >= '2026-01-01' OR closed_at < '2026-04-01'"
    assert _ranges(sql) == []


def test_not_between_is_a_candidate_with_negated_context():
    sql = "SELECT * FROM deals WHERE closed_at NOT BETWEEN '2026-01-01' AND '2026-04-01'"
    a = analyze(sql, D, SCHEMA)
    assert [(r.start, r.end, r.shape) for r in a.ranges] == [
        ("main.L1", "main.L2", RangeShape.INCLUSIVE)
    ]
    assert all(lit.context == [C.NEGATED] for lit in a.literals)


def test_case_bound_does_not_pair_with_where_bound():
    sql = """
    SELECT SUM(CASE WHEN closed_at >= '2026-01-01' THEN 1 END) FROM deals
    WHERE closed_at < '2026-04-01'
    """
    a = analyze(sql, D, SCHEMA)
    assert a.ranges == []
    assert a.literal("main.L1").context == [C.CASE_CONDITION]


def test_negated_window_and_two_or_windows():
    sql = """
    SELECT * FROM deals WHERE NOT (closed_at >= 'a' AND closed_at < 'b')
      AND ((closed_at >= 'x' AND closed_at < 'y') OR (closed_at >= 'p' AND closed_at < 'q'))
    """
    a = analyze(sql, D, SCHEMA)
    assert [(r.start, r.end, r.shape) for r in a.ranges] == [
        ("main.L1", "main.L2", RangeShape.HALF_OPEN),
        ("main.L3", "main.L4", RangeShape.HALF_OPEN),
        ("main.L5", "main.L6", RangeShape.HALF_OPEN),
    ]
    by_value = {lit.value: lit.context for lit in a.literals}
    assert by_value["a"] == by_value["b"] == [C.NEGATED]
    assert {tuple(by_value[v]) for v in "xypq"} == {(C.OR_BRANCH,)}


def test_nested_and_with_parentheses_is_one_chain():
    sql = """
    SELECT * FROM deals
    WHERE (closed_at >= '2026-01-01' AND region = 'A')
      AND (amount > 0 AND (closed_at < '2026-04-01'))
    """
    assert _ranges(sql) == [("main.L1", "main.L4", RangeShape.HALF_OPEN)]


def test_bound_under_not_inside_chain_is_not_paired():
    sql = "SELECT * FROM deals WHERE closed_at >= '2026-01-01' AND NOT (closed_at < '2026-04-01')"
    assert _ranges(sql) == []


def test_windows_in_different_ctes_and_columns_do_not_block_each_other():
    sql = """
    WITH a AS (SELECT * FROM deals WHERE closed_at >= '2026-01-01' AND closed_at < '2026-02-01'
                                     AND amount >= 10 AND amount <= 20),
         b AS (SELECT * FROM deals WHERE closed_at >= '2025-01-01' AND closed_at < '2025-02-01')
    SELECT * FROM a UNION ALL SELECT * FROM b
    """
    assert _ranges(sql) == [
        ("a.L1", "a.L2", RangeShape.HALF_OPEN),
        ("a.L3", "a.L4", RangeShape.INCLUSIVE),
        ("b.L1", "b.L2", RangeShape.HALF_OPEN),
    ]


def test_two_bounds_on_one_side_in_one_chain_is_ambiguous():
    sql = """
    SELECT * FROM deals
    WHERE closed_at >= '2026-01-01' AND closed_at >= '2026-02-01' AND closed_at < '2026-04-01'
    """
    assert _ranges(sql) == []


def test_different_column_expressions_do_not_pair():
    sql = (
        "SELECT * FROM deals"
        " WHERE closed_at >= '2026-01-01' AND CAST(closed_at AS DATE) < '2026-04-01'"
    )
    assert _ranges(sql) == []


def test_value_wrapped_bounds_pair_when_each_bound_has_one_literal():
    sql = """
    SELECT * FROM deals WHERE closed_at >= strptime('01-01-2026', '%m-%d-%Y')
      AND closed_at < strptime('04-01-2026', '%m-%d-%Y')
    """
    assert _ranges(sql) == [("main.L1", "main.L2", RangeShape.HALF_OPEN)]


def test_bound_with_two_literals_is_not_paired():
    sql = "SELECT * FROM deals WHERE amount >= 5 * 1e3 AND amount < 9000"
    assert _ranges(sql) == []
