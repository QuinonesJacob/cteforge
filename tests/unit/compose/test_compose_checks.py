"""Composition checks: unknown_alias, unused_alias, joins, fan_out_risk (DESIGN §17)."""

from __future__ import annotations

import duckdb
import pytest

from cteforge.compose import JoinUse, Part, compose
from cteforge.domain.dialects import Dialect
from cteforge.domain.issues import IssueCode, Severity
from cteforge.domain.models import Definition


def part(
    alias: str,
    *,
    grain: str | None = None,
    join_keys: list[str] | None = None,
    outputs: list[str] | None = None,
    sql: str | None = None,
) -> Part:
    definition = Definition(
        id=alias,
        title=alias,
        source_dialect=Dialect.DUCKDB,
        template="SELECT 1",
        grain=grain,
        join_keys=join_keys or [],
        outputs=outputs or [],
    )
    return Part(alias=alias, definition=definition, sql=sql or f"SELECT * FROM src_{alias}")


ACCOUNTS = part(
    "acct",
    grain="one row per account",
    join_keys=["account_id"],
    outputs=["account_id", "region"],
)
DEALS = part(
    "deals",
    grain="one row per deal",
    join_keys=["deal_id", "account_id"],
    outputs=["deal_id", "account_id", "amount"],
)
REVENUE = part(
    "rev",
    grain="One row per   account",  # same grain as ACCOUNTS after normalizing
    join_keys=["account_id"],
    outputs=["account_id", "revenue"],
)
NOTES = part("notes", outputs=["account_id", "note"])  # no grain, no join keys


def codes(issues) -> list[IssueCode]:
    return [i.code for i in issues]


def run(select: str, *parts: Part):
    return compose(list(parts), select, Dialect.DUCKDB)


# ── unknown_alias ───────────────────────────────────────────────────────────────────


def test_unknown_qualifier_is_an_error_issue() -> None:
    result = run("SELECT x.region, a.region FROM acct AS a", ACCOUNTS)
    assert codes(result.issues) == [IssueCode.UNKNOWN_ALIAS]
    assert result.issues[0].severity is Severity.ERROR
    assert "'x'" in result.issues[0].message


def test_unknown_table_in_from_is_a_raw_table_not_an_error() -> None:
    result = run(
        "SELECT a.region, m.x FROM acct AS a JOIN mystery AS m ON m.id = a.account_id", ACCOUNTS
    )
    assert IssueCode.UNKNOWN_ALIAS not in codes(result.issues)
    assert result.raw_tables == ["mystery"]


@pytest.mark.parametrize(
    "select",
    [
        "SELECT acct.region FROM acct",  # table name as qualifier
        "SELECT a.region FROM acct AS a WHERE EXISTS "
        "(SELECT 1 FROM deals AS d WHERE d.account_id = a.account_id)",  # correlated
        "WITH big AS (SELECT * FROM acct) SELECT big.region FROM big",
        "SELECT s.account_id FROM (SELECT account_id FROM acct) AS s",
    ],
)
def test_visible_qualifiers_are_fine(select: str) -> None:
    result = run(select, ACCOUNTS, DEALS)
    assert IssueCode.UNKNOWN_ALIAS not in codes(result.issues)


def test_qualifier_from_outer_query_not_visible_inside_cte() -> None:
    result = run(
        "WITH c AS (SELECT a.region FROM deals) SELECT * FROM acct AS a, c", ACCOUNTS, DEALS
    )
    assert IssueCode.UNKNOWN_ALIAS in codes(result.issues)


def test_struct_access_on_part_output_is_not_flagged() -> None:
    result = run("SELECT region.code FROM acct", ACCOUNTS)
    assert IssueCode.UNKNOWN_ALIAS not in codes(result.issues)


# ── unused_alias ────────────────────────────────────────────────────────────────────


def test_unused_alias_warning() -> None:
    result = run("SELECT * FROM acct", ACCOUNTS, NOTES)
    assert codes(result.issues) == [IssueCode.UNUSED_ALIAS]
    assert result.issues[0].severity is Severity.WARNING
    assert "'notes'" in result.issues[0].message


def test_alias_used_only_in_subquery_counts_as_used() -> None:
    result = run(
        "SELECT * FROM acct WHERE account_id IN (SELECT account_id FROM notes)", ACCOUNTS, NOTES
    )
    assert IssueCode.UNUSED_ALIAS not in codes(result.issues)


def test_alias_shadowed_by_nested_with_is_unused() -> None:
    result = run(
        "SELECT * FROM acct WHERE account_id IN "
        "(WITH notes AS (SELECT 1 AS account_id) SELECT account_id FROM notes)",
        ACCOUNTS,
        NOTES,
    )
    assert IssueCode.UNUSED_ALIAS in codes(result.issues)


# ── Joins ───────────────────────────────────────────────────────────────────────────


def test_known_join_resolves_table_aliases() -> None:
    result = run(
        "SELECT a.region, SUM(d.amount) FROM acct AS a "
        "JOIN deals AS d ON d.account_id = a.account_id GROUP BY a.region",
        ACCOUNTS,
        DEALS,
    )
    assert result.joins == [
        JoinUse(left="acct", right="deals", keys=[("account_id", "account_id")], known=True)
    ]
    # Known join: no unknown_join. Different grains joined directly still warn.
    assert codes(result.issues) == [IssueCode.FAN_OUT_RISK]


def test_join_on_undeclared_key_is_unknown_join() -> None:
    result = run(
        "SELECT * FROM acct AS a JOIN rev AS r "
        "ON r.revenue = a.region AND r.account_id = a.account_id",
        ACCOUNTS,
        REVENUE,
    )
    assert result.joins == [
        JoinUse(
            left="acct",
            right="rev",
            keys=[("region", "revenue"), ("account_id", "account_id")],
            known=False,
        )
    ]
    assert codes(result.issues) == [IssueCode.UNKNOWN_JOIN]
    assert result.issues[0].severity is Severity.WARNING


def test_join_with_non_key_filter_in_on_still_known() -> None:
    result = run(
        "SELECT * FROM acct AS a JOIN rev AS r ON a.account_id = r.account_id AND r.revenue > 0",
        ACCOUNTS,
        REVENUE,
    )
    assert result.joins[0].known is True
    assert result.issues == []


def test_join_without_declared_keys_on_one_side_is_unknown() -> None:
    result = run(
        "SELECT * FROM acct AS a JOIN notes AS n ON n.account_id = a.account_id", ACCOUNTS, NOTES
    )
    assert result.joins[0].known is False
    assert codes(result.issues) == [IssueCode.UNKNOWN_JOIN]


def test_using_join() -> None:
    result = run("SELECT * FROM acct JOIN rev USING (account_id)", ACCOUNTS, REVENUE)
    assert result.joins == [
        JoinUse(left="acct", right="rev", keys=[("account_id", "account_id")], known=True)
    ]


def test_comma_join_takes_keys_from_where() -> None:
    result = run(
        "SELECT * FROM acct AS a, rev AS r WHERE a.account_id = r.account_id AND r.revenue > 1",
        ACCOUNTS,
        REVENUE,
    )
    assert result.joins == [
        JoinUse(left="acct", right="rev", keys=[("account_id", "account_id")], known=True)
    ]


def test_cross_join_between_aliases_is_unknown() -> None:
    result = run("SELECT * FROM acct CROSS JOIN rev", ACCOUNTS, REVENUE)
    assert result.joins == [JoinUse(left="acct", right="rev", keys=[], known=False)]
    assert codes(result.issues) == [IssueCode.UNKNOWN_JOIN]


def test_unqualified_key_resolved_through_outputs() -> None:
    result = run(
        "SELECT * FROM acct AS a JOIN deals AS d ON deal_id = a.account_id", ACCOUNTS, DEALS
    )
    assert result.joins[0].keys == [("account_id", "deal_id")]


def test_three_way_join_reports_each_left_side() -> None:
    result = run(
        "SELECT a.region, SUM(d.amount) FROM acct AS a "
        "JOIN rev AS r ON r.account_id = a.account_id "
        "JOIN deals AS d ON d.account_id = r.account_id GROUP BY 1",
        ACCOUNTS,
        REVENUE,
        DEALS,
    )
    assert [(j.left, j.right, j.known) for j in result.joins] == [
        ("acct", "rev", True),
        ("rev", "deals", True),
    ]
    assert codes(result.issues) == [IssueCode.FAN_OUT_RISK]  # rev/deals grains differ


def test_join_to_raw_table_is_reported_without_warning() -> None:
    result = run(
        "SELECT * FROM acct AS a LEFT JOIN crm.owners AS o ON o.account_id = a.account_id",
        ACCOUNTS,
    )
    assert result.joins == [
        JoinUse(left="acct", right="crm.owners", keys=[("account_id", "account_id")], known=False)
    ]
    assert result.issues == []
    assert result.raw_tables == ["crm.owners"]


def test_join_inside_final_cte_and_own_cte_names() -> None:
    result = run(
        "WITH j AS (SELECT a.account_id FROM acct AS a JOIN rev AS r USING (account_id)) "
        "SELECT * FROM j JOIN deals AS d ON d.account_id = j.account_id GROUP BY ALL",
        ACCOUNTS,
        REVENUE,
        DEALS,
    )
    # Outer query first, then the SELECTs nested in it (breadth-first, deterministic).
    assert [(j.left, j.right, j.known) for j in result.joins] == [
        ("j", "deals", False),
        ("acct", "rev", True),
    ]
    assert IssueCode.UNKNOWN_JOIN not in codes(result.issues)  # j is not an alias


# ── fan_out_risk ────────────────────────────────────────────────────────────────────


def test_different_grain_without_group_by_is_fan_out_risk() -> None:
    result = run(
        "SELECT a.region, d.amount FROM acct AS a JOIN deals AS d ON d.account_id = a.account_id",
        ACCOUNTS,
        DEALS,
    )
    assert codes(result.issues) == [IssueCode.FAN_OUT_RISK]
    assert result.issues[0].severity is Severity.WARNING
    assert "one row per deal" in result.issues[0].message


@pytest.mark.parametrize(
    "select",
    [
        "SELECT a.region, SUM(d.amount) FROM acct AS a JOIN deals AS d "
        "ON d.account_id = a.account_id GROUP BY a.region",
        "SELECT SUM(d.amount) FROM acct AS a JOIN deals AS d ON d.account_id = a.account_id",
        "SELECT region, SUM(amount) FROM (SELECT a.region, d.amount FROM acct AS a "
        "JOIN deals AS d ON d.account_id = a.account_id) AS s GROUP BY region",
        "WITH j AS (SELECT a.region, d.amount FROM acct AS a JOIN deals AS d "
        "ON d.account_id = a.account_id) SELECT SUM(amount) FROM j",
    ],
)
def test_aggregating_after_the_join_still_warns(select: str) -> None:
    # Aggregating the joined rows does not undo the fan-out: SUM over the coarser side's
    # columns is still inflated (see the DuckDB check below).
    assert IssueCode.FAN_OUT_RISK in codes(run(select, ACCOUNTS, DEALS).issues)


def test_post_join_sum_is_really_inflated_on_duckdb() -> None:
    rev = part(
        "rev",
        grain="per account",
        join_keys=["account_id"],
        outputs=["account_id", "revenue"],
        sql="SELECT 1 AS account_id, 10 AS revenue",
    )
    hc = part(
        "hc",
        grain="per user",
        join_keys=["account_id", "user_id"],
        outputs=["account_id", "user_id"],
        sql="SELECT 1 AS account_id, 5 AS user_id UNION ALL SELECT 1, 6",
    )
    result = run(
        "SELECT rev.account_id, SUM(rev.revenue) AS revenue FROM rev "
        "JOIN hc ON rev.account_id = hc.account_id GROUP BY rev.account_id",
        rev,
        hc,
    )
    assert duckdb.sql(result.sql).fetchall() == [(1, 20)]  # 10 counted twice
    assert IssueCode.FAN_OUT_RISK in codes(result.issues)


@pytest.mark.parametrize(
    "select",
    [
        "SELECT a.region, d.total FROM acct AS a JOIN (SELECT account_id, SUM(amount) AS total "
        "FROM deals GROUP BY account_id) AS d ON d.account_id = a.account_id",
        "WITH d AS (SELECT account_id, SUM(amount) AS total FROM deals GROUP BY account_id) "
        "SELECT a.region, d.total FROM acct AS a JOIN d ON d.account_id = a.account_id",
    ],
)
def test_aggregating_to_a_common_grain_before_joining_silences_fan_out(select: str) -> None:
    assert IssueCode.FAN_OUT_RISK not in codes(run(select, ACCOUNTS, DEALS).issues)


def test_window_aggregate_does_not_count_as_aggregation() -> None:
    result = run(
        "SELECT a.region, SUM(d.amount) OVER (PARTITION BY a.region) FROM acct AS a "
        "JOIN deals AS d ON d.account_id = a.account_id",
        ACCOUNTS,
        DEALS,
    )
    assert IssueCode.FAN_OUT_RISK in codes(result.issues)


def test_same_grain_or_missing_grain_is_not_fan_out() -> None:
    same = run("SELECT * FROM acct JOIN rev USING (account_id)", ACCOUNTS, REVENUE)
    missing = run(
        "SELECT * FROM acct AS a JOIN notes AS n ON n.account_id = a.account_id", ACCOUNTS, NOTES
    )
    assert IssueCode.FAN_OUT_RISK not in codes(same.issues)
    assert IssueCode.FAN_OUT_RISK not in codes(missing.issues)


def test_fan_out_reported_once_per_pair() -> None:
    result = run(
        "SELECT * FROM acct AS a JOIN deals AS d1 ON d1.account_id = a.account_id "
        "UNION ALL SELECT * FROM acct AS a JOIN deals AS d2 ON d2.account_id = a.account_id",
        ACCOUNTS,
        DEALS,
    )
    assert codes(result.issues).count(IssueCode.FAN_OUT_RISK) == 1
    assert len(result.joins) == 2


# ── join key pairing ────────────────────────────────────────────────────────────────


def test_join_on_two_different_declared_keys_is_unknown() -> None:
    users = part(
        "hc",
        grain="one row per user",
        join_keys=["account_id", "user_id"],
        outputs=["account_id", "user_id"],
    )
    result = run("SELECT * FROM rev JOIN hc ON rev.account_id = hc.user_id", REVENUE, users)
    assert result.joins == [
        JoinUse(left="rev", right="hc", keys=[("account_id", "user_id")], known=False)
    ]
    assert IssueCode.UNKNOWN_JOIN in codes(result.issues)


def test_same_declared_key_differing_in_case_is_known() -> None:
    result = run(
        "SELECT * FROM acct JOIN rev ON acct.ACCOUNT_ID = rev.account_id", ACCOUNTS, REVENUE
    )
    assert result.joins[0].known is True
    assert IssueCode.UNKNOWN_JOIN not in codes(result.issues)


# ── table functions are raw reads ───────────────────────────────────────────────────


def test_table_function_in_scalar_subquery_is_a_raw_table() -> None:
    result = run("SELECT *, (SELECT COUNT(*) FROM read_text('/etc/hosts')) AS n FROM rev", REVENUE)
    assert result.raw_tables == ["read_text()"]


def test_joined_table_function_is_raw_and_named_in_join_use() -> None:
    result = run("SELECT * FROM rev JOIN read_csv('/etc/passwd') AS t ON TRUE", REVENUE)
    assert result.raw_tables == ["read_csv()"]
    assert result.joins == [JoinUse(left="rev", right="read_csv()", keys=[], known=False)]


def test_snowflake_table_wrapper_function_is_raw() -> None:
    p = part("rev", sql="SELECT 1 AS a")
    result = compose(
        [p], "SELECT * FROM rev, TABLE(RESULT_SCAN(LAST_QUERY_ID())) AS r", Dialect.SNOWFLAKE
    )
    assert result.raw_tables == ["result_scan()"]


def test_row_generators_are_not_raw_tables() -> None:
    result = run("SELECT * FROM rev, range(3) AS r, unnest([1, 2]) AS u", REVENUE)
    assert result.raw_tables == []


# ── nested WITH visibility in the final SELECT ──────────────────────────────────────


def test_nested_cte_body_reading_alias_counts_as_use() -> None:
    rev = part("rev", sql="SELECT 1 AS account_id", outputs=["account_id"])
    result = run(
        "SELECT * FROM (WITH rev AS (SELECT account_id + 1 AS account_id FROM rev) "
        "SELECT * FROM rev) AS s",
        rev,
    )
    assert IssueCode.UNUSED_ALIAS not in codes(result.issues)
    assert duckdb.sql(result.sql).fetchall() == [(2,)]
