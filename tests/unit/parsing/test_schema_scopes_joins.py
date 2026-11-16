"""Schema lookups, scope naming, IN-list groups and joins."""

from cteforge.domain.dialects import Dialect
from cteforge.parsing.analyze import analyze, find_literals, parse, referenced_tables

D = Dialect.DUCKDB


def _column(sql: str, schema: dict[str, dict[str, str]]):
    return analyze(sql, D, schema).literals[0].column


# ── Schema lookups ──────────────────────────────────────────────────────────────────


def test_mixed_case_sql_matches_lower_case_schema():
    col = _column(
        "SELECT * FROM Deals WHERE Deals.Closed_At >= '2026-01-01'",
        {"deals": {"closed_at": "TIMESTAMP"}},
    )
    assert (col.table, col.column, col.type) == ("deals", "closed_at", "TIMESTAMP")


def test_lower_case_sql_matches_mixed_case_schema():
    col = _column(
        "SELECT * FROM deals WHERE closed_at >= '2026-01-01'",
        {"DEALS": {"Closed_At": "TIMESTAMP_NTZ"}},
    )
    assert col.type == "TIMESTAMP_NTZ"


def test_qualified_table_prefers_qualified_key():
    schema = {"analytics.deals": {"closed_at": "TIMESTAMP"}, "deals": {"closed_at": "DATE"}}
    col = _column("SELECT * FROM Analytics.Deals AS d WHERE d.closed_at >= '2026-01-01'", schema)
    assert (col.table, col.type) == ("analytics.deals", "TIMESTAMP")
    col = _column("SELECT * FROM deals WHERE closed_at >= '2026-01-01'", schema)
    assert (col.table, col.type) == ("deals", "DATE")


def test_qualified_table_falls_back_to_unambiguous_bare_key():
    col = _column(
        "SELECT * FROM analytics.deals WHERE closed_at >= '2026-01-01'",
        {"deals": {"closed_at": "TIMESTAMP"}, "staging.other": {"x": "INT"}},
    )
    assert (col.table, col.type) == ("analytics.deals", "TIMESTAMP")


def test_bare_table_against_ambiguous_qualified_keys_is_unknown():
    col = _column(
        "SELECT * FROM deals WHERE closed_at >= '2026-01-01'",
        {"a.deals": {"closed_at": "TIMESTAMP"}, "b.deals": {"closed_at": "DATE"}},
    )
    assert col.table == "deals" and col.type is None


def test_wrong_schema_qualifier_does_not_match():
    col = _column(
        "SELECT * FROM staging.deals WHERE closed_at >= '2026-01-01'",
        {"analytics.deals": {"closed_at": "TIMESTAMP"}},
    )
    assert col.type is None


def test_quoted_identifiers_match_exactly():
    schema = {"Deals": {"Closed": "DATE"}}
    assert _column('SELECT * FROM "Deals" WHERE "Closed" > \'2026\'', schema).type == "DATE"
    assert _column('SELECT * FROM "deals" WHERE "Closed" > \'2026\'', schema).type is None


def test_referenced_tables_are_qualified_and_exclude_ctes():
    sql = """
    WITH Recent AS (SELECT * FROM Analytics.Deals)
    SELECT * FROM recent JOIN "Stg".Accounts AS a ON TRUE JOIN db.s.t ON TRUE
    """
    assert referenced_tables(sql, D) == ["Stg.accounts", "analytics.deals", "db.s.t"]
    a = analyze(sql, D)
    assert a.tables == ["Stg.accounts", "analytics.deals", "db.s.t"]
    assert a.ctes[0].reads == ["analytics.deals"]


# ── Scopes and IDs ──────────────────────────────────────────────────────────────────


def test_cte_named_main_does_not_collide_with_outer_query():
    sql = "WITH main AS (SELECT * FROM deals WHERE amount > 1) SELECT * FROM main WHERE amount < 5"
    ids = [lit.id for lit in analyze(sql, D).literals]
    assert ids == ["main.L1", "main_2.L1"]
    assert [f.id for f in find_literals(parse(sql, D))] == ids


def test_outer_scope_skips_every_taken_name():
    sql = """
    WITH MAIN AS (SELECT * FROM t WHERE a = 1), main_2 AS (SELECT * FROM t WHERE a = 2)
    SELECT * FROM main_2 WHERE a = 3
    """
    assert [lit.id for lit in analyze(sql, D).literals] == ["MAIN.L1", "main_2.L1", "main_3.L1"]


def test_in_list_groups_are_numbered_per_scope():
    sql = """
    WITH a AS (SELECT * FROM t WHERE x IN (1, 2) AND y IN (3))
    SELECT * FROM a WHERE z IN (4, 5)
    """
    groups = [(lit.id, lit.in_list) for lit in analyze(sql, D).literals]
    assert groups == [
        ("a.L1", "a.IN1"),
        ("a.L2", "a.IN1"),
        ("a.L3", "a.IN2"),
        ("main.L1", "main.IN1"),
        ("main.L2", "main.IN1"),
    ]


def test_ids_are_deterministic_across_parses():
    sql = "SELECT * FROM t WHERE a IN (1, 2) AND b = UPPER('x') AND c BETWEEN 1 AND 2"
    first = analyze(sql, D).model_dump()
    assert analyze(sql, D).model_dump() == first


# ── Joins ───────────────────────────────────────────────────────────────────────────


def test_joins_in_sample_query(sample_sql, sample_schema):
    joins = [
        (j.scope, j.left_table, j.right_table, j.join_type, j.keys, j.on_sql)
        for j in analyze(sample_sql, D, sample_schema).joins
    ]
    assert joins == [
        (
            "deals_in_region",
            "closed_deals",
            "accounts",
            "inner",
            [("account_id", "account_id")],
            "a.account_id = cd.account_id",
        ),
        (
            "main",
            "deals_in_region",
            "collected",
            "left",
            [("deal_id", "deal_id")],
            "c.deal_id = dr.deal_id",
        ),
    ]


def test_join_variants():
    sql = """
    SELECT * FROM Analytics.Deals d
    LEFT JOIN accounts a ON a.id = d.account_id AND a.active = TRUE
    JOIN deals e USING (region)
    CROSS JOIN calendar
    JOIN (SELECT region AS r FROM deals) AS sq ON sq.r = d.region
    """
    joins = [(j.left_table, j.right_table, j.join_type, j.keys) for j in analyze(sql, D).joins]
    assert joins == [
        ("analytics.deals", "accounts", "left", [("account_id", "id")]),
        ("analytics.deals", "deals", "inner", [("region", "region")]),
        ("analytics.deals", "calendar", "cross", []),
        ("analytics.deals", "sq", "inner", [("region", "r")]),
    ]
    assert analyze(sql, D).joins[1].on_sql == "USING (region)"
