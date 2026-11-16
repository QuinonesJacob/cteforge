"""JoinInfo base tables: resolving join sides through CTEs and derived tables to the base
table their key columns pass through from (join-map mining, DESIGN §3)."""

from pathlib import Path

from cteforge.domain.dialects import Dialect
from cteforge.parsing.analyze import analyze

D = Dialect.DUCKDB
EXAMPLES = Path(__file__).resolve().parents[3] / "examples"


def _bases(sql: str) -> list[tuple[str | None, str | None, list[tuple[str, str]]]]:
    return [(j.left_base_table, j.right_base_table, j.base_keys) for j in analyze(sql, D).joins]


def test_realization_rate_resolves_both_cte_joins():
    sql = (EXAMPLES / "queries" / "realization_rate.sql").read_text()
    joins = analyze(sql, D).joins
    assert [(j.left_table, j.right_table, j.keys) for j in joins] == [
        ("closed_deals", "accounts", [("account_id", "account_id")]),
        ("deals_in_region", "collected", [("deal_id", "deal_id")]),
    ]
    assert _bases(sql) == [
        ("deals", "accounts", [("account_id", "account_id")]),
        ("deals", "invoices", [("deal_id", "deal_id")]),
    ]


def test_base_table_join_mirrors_its_own_tables_and_keys():
    sql = "SELECT * FROM deals d JOIN accounts a ON a.account_id = d.account_id"
    assert _bases(sql) == [("deals", "accounts", [("account_id", "account_id")])]


def test_renamed_pass_through_column_maps_to_the_base_column():
    sql = """
    WITH acc AS (SELECT a.account_id AS acct, a.region FROM accounts a)
    SELECT * FROM deals d JOIN acc ON acc.acct = d.account_id
    """
    (join,) = analyze(sql, D).joins
    assert join.keys == [("account_id", "acct")]
    assert (join.left_base_table, join.right_base_table) == ("deals", "accounts")
    assert join.base_keys == [("account_id", "account_id")]


def test_computed_key_column_does_not_resolve():
    sql = """
    WITH acc AS (SELECT a.account_id + 0 AS account_id FROM accounts a)
    SELECT * FROM deals d JOIN acc ON acc.account_id = d.account_id
    """
    assert _bases(sql) == [("deals", None, [])]


def test_keys_from_two_base_tables_do_not_resolve():
    sql = """
    WITH both_ AS (
      SELECT d.deal_id, a.region FROM deals d JOIN accounts a ON a.account_id = d.account_id
    )
    SELECT * FROM regions r JOIN both_ b ON b.region = r.region AND b.deal_id = r.deal_id
    """
    joins = analyze(sql, D).joins
    assert (joins[0].left_base_table, joins[0].base_keys) == (
        "deals",
        [("account_id", "account_id")],
    )
    assert (joins[1].left_base_table, joins[1].right_base_table, joins[1].base_keys) == (
        "regions",
        None,
        [],
    )


def test_star_and_derived_table_and_using():
    sql = """
    WITH d2 AS (SELECT * FROM deals)
    SELECT *
    FROM d2
    JOIN (SELECT account_id FROM accounts) AS sq USING (account_id)
    """
    assert _bases(sql) == [("deals", "accounts", [("account_id", "account_id")])]


def test_cte_shadowing_its_base_table_resolves_to_the_table():
    sql = """
    WITH deals AS (SELECT * FROM deals WHERE amount > 0)
    SELECT * FROM deals JOIN accounts a ON a.account_id = deals.account_id
    """
    (join,) = analyze(sql, D).joins
    assert join.left_table == "deals"  # the CTE, which reads base table deals
    assert join.base_keys == [("account_id", "account_id")]


def test_cross_join_has_no_base_keys():
    assert _bases("SELECT * FROM deals CROSS JOIN calendar") == [("deals", "calendar", [])]
