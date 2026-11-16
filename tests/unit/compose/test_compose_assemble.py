"""compose(): hoisting, renaming, WITH assembly, final-SELECT validation (DESIGN §17)."""

from __future__ import annotations

import duckdb
import pytest
from sqlglot import exp

from cteforge.compose import ComposedQuery, Part, compose
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import RenderError
from cteforge.domain.issues import IssueCode
from cteforge.domain.models import Definition
from cteforge.parsing.analyze import parse


def definition(
    id_: str,
    *,
    grain: str | None = None,
    join_keys: list[str] | None = None,
    outputs: list[str] | None = None,
    dialect: Dialect = Dialect.DUCKDB,
) -> Definition:
    return Definition(
        id=id_,
        title=id_.replace("_", " "),
        source_dialect=dialect,
        template="SELECT 1",
        grain=grain,
        join_keys=join_keys or [],
        outputs=outputs or [],
    )


def part(alias: str, sql: str, **kwargs) -> Part:
    return Part(alias=alias, definition=definition(alias, **kwargs), sql=sql)


def cte_list(sql: str, dialect: Dialect) -> list[str]:
    tree = parse(sql, dialect)
    with_ = tree.args.get("with_")
    return [c.alias_or_name for c in with_.expressions] if with_ else []


def unresolved_tables(sql: str, dialect: Dialect) -> set[str]:
    """Unqualified table names in `sql` that are not a CTE visible at that point."""
    tree = parse(sql, dialect)
    ctes = tree.args["with_"].expressions
    names = [c.alias_or_name for c in ctes]
    result: set[str] = set()
    for table in tree.find_all(exp.Table):
        owner = next((c for c in ctes if table.find_ancestor(exp.CTE) is c), None)
        visible = names[: ctes.index(owner)] if owner is not None else names
        if table.name not in visible:
            result.add(table.name)
    return result


REVENUE = part(
    "rev",
    """
    WITH recent AS (SELECT account_id, amount FROM deals WHERE closed_at >= '2026-01-01'),
         totals AS (SELECT account_id, SUM(amount) AS revenue FROM recent GROUP BY account_id)
    SELECT a.account_id, totals.revenue
    FROM accounts AS a
    JOIN totals ON totals.account_id = a.account_id
    WHERE a.account_id IN (SELECT account_id FROM recent)
    """,
    grain="one row per account",
    join_keys=["account_id"],
    outputs=["account_id", "revenue"],
)
HEADCOUNT = part(
    "hc",
    """
    WITH recent AS (SELECT account_id, user_id FROM seats WHERE active)
    SELECT account_id, COUNT(*) AS seats FROM recent GROUP BY account_id
    """,
    grain="one row per account",
    join_keys=["account_id"],
    outputs=["account_id", "seats"],
)


# ── No final SELECT ─────────────────────────────────────────────────────────────────


def test_no_select_returns_single_part_unchanged() -> None:
    result = compose([REVENUE], None, Dialect.DUCKDB)
    assert result.sql == REVENUE.sql
    assert (result.issues, result.raw_tables, result.joins) == ([], [], [])


@pytest.mark.parametrize("parts", [[], [REVENUE, HEADCOUNT]])
def test_no_select_requires_exactly_one_part(parts: list[Part]) -> None:
    with pytest.raises(RenderError, match="exactly one"):
        compose(parts, "  ", Dialect.DUCKDB)


def test_select_requires_a_part() -> None:
    with pytest.raises(RenderError, match="at least one"):
        compose([], "SELECT 1", Dialect.DUCKDB)


# ── Hoisting ────────────────────────────────────────────────────────────────────────


def test_inner_ctes_are_hoisted_renamed_and_ordered() -> None:
    result = compose(
        [REVENUE, HEADCOUNT],
        "SELECT r.account_id, r.revenue, h.seats FROM rev AS r JOIN hc AS h "
        "ON h.account_id = r.account_id",
        Dialect.DUCKDB,
    )
    assert cte_list(result.sql, Dialect.DUCKDB) == [
        "rev__recent",
        "rev__totals",
        "rev",
        "hc__recent",
        "hc",
    ]
    # Every reference (nested CTE, JOIN, IN-subquery) points at a visible CTE or real table.
    assert unresolved_tables(result.sql, Dialect.DUCKDB) == {"deals", "accounts", "seats"}
    assert result.issues == []


def test_renamed_reference_keeps_old_name_as_alias_for_qualifiers() -> None:
    result = compose([REVENUE], "SELECT * FROM rev", Dialect.DUCKDB)
    tree = parse(result.sql, Dialect.DUCKDB)
    rev_body = next(c.this for c in tree.args["with_"].expressions if c.alias == "rev")
    joined = rev_body.args["joins"][0].this
    assert (joined.name, joined.alias) == ("rev__totals", "totals")
    # `totals.revenue` is still a valid qualifier in the part's main query.
    assert "totals" in {c.table for c in rev_body.find_all(exp.Column)}


def test_cte_reading_a_table_of_the_same_name_keeps_the_table() -> None:
    p = part("act", "WITH deals AS (SELECT * FROM deals WHERE amount > 0) SELECT * FROM deals")
    result = compose([p], "SELECT * FROM act", Dialect.DUCKDB)
    tree = parse(result.sql, Dialect.DUCKDB)
    ctes = {c.alias: c.this for c in tree.args["with_"].expressions}
    assert list(ctes) == ["act__deals", "act"]
    assert ctes["act__deals"].args["from_"].this.name == "deals"  # the real table
    assert ctes["act"].args["from_"].this.name == "act__deals"
    assert result.raw_tables == []


def test_forward_reference_between_inner_ctes_is_a_table() -> None:
    p = part("x", "WITH a AS (SELECT * FROM b), b AS (SELECT 1 AS v) SELECT * FROM a")
    result = compose([p], "SELECT * FROM x", Dialect.DUCKDB)
    assert "b" in unresolved_tables(result.sql, Dialect.DUCKDB)


def test_nested_with_in_subquery_is_left_in_place_and_shadows() -> None:
    p = part(
        "x",
        "WITH t AS (SELECT 1 AS v) "
        "SELECT * FROM t WHERE v IN (WITH t AS (SELECT 2 AS v) SELECT v FROM t)",
    )
    result = compose([p], "SELECT * FROM x", Dialect.DUCKDB)
    tree = parse(result.sql, Dialect.DUCKDB)
    names = [t.name for t in tree.find_all(exp.Table)]
    assert sorted(names) == sorted(["x__t", "t", "x"])  # inner `FROM t` keeps nested CTE


def test_hoisted_name_avoids_real_table_with_that_name() -> None:
    other = part("o", "SELECT * FROM rev__recent")  # a real table that looks hoisted
    result = compose([REVENUE, other], "SELECT * FROM rev, o", Dialect.DUCKDB)
    names = cte_list(result.sql, Dialect.DUCKDB)
    assert names == ["rev__recent_2", "rev__totals", "rev", "o"]
    assert "rev__recent" in unresolved_tables(result.sql, Dialect.DUCKDB)


def test_hoisted_name_avoids_final_select_cte() -> None:
    result = compose(
        [REVENUE],
        "WITH rev__totals AS (SELECT 1 AS k) SELECT * FROM rev, rev__totals",
        Dialect.DUCKDB,
    )
    assert cte_list(result.sql, Dialect.DUCKDB) == [
        "rev__recent",
        "rev__totals_2",
        "rev",
        "rev__totals",
    ]


def test_odd_cte_names_are_sanitized() -> None:
    p = part("x", 'WITH "My Step" AS (SELECT 1 AS v) SELECT * FROM "My Step"')
    result = compose([p], "SELECT * FROM x", Dialect.DUCKDB)
    assert cte_list(result.sql, Dialect.DUCKDB) == ["x__my_step", "x"]


def test_part_whose_main_query_is_a_union() -> None:
    p = part(
        "u",
        "WITH a AS (SELECT 1 AS v), b AS (SELECT 2 AS v) SELECT v FROM a UNION ALL SELECT v FROM b",
    )
    result = compose([p], "SELECT SUM(v) AS total FROM u", Dialect.DUCKDB)
    assert cte_list(result.sql, Dialect.DUCKDB) == ["u__a", "u__b", "u"]
    assert duckdb.sql(result.sql).fetchall() == [(3,)]


def test_alias_equal_to_a_table_a_part_reads_is_rejected() -> None:
    p = part("deals", "SELECT * FROM deals")
    with pytest.raises(RenderError, match="alias would hide that table"):
        compose([p], "SELECT * FROM deals", Dialect.DUCKDB)


def test_qualified_table_with_alias_name_is_fine() -> None:
    p = part("deals", "SELECT * FROM sales.deals")
    result = compose([p], "SELECT * FROM deals", Dialect.DUCKDB)
    assert "sales" not in cte_list(result.sql, Dialect.DUCKDB)
    assert result.raw_tables == []


def test_duplicate_alias_rejected() -> None:
    with pytest.raises(RenderError, match="used twice"):
        compose([REVENUE, REVENUE], "SELECT * FROM rev", Dialect.DUCKDB)


@pytest.mark.parametrize(
    "sql",
    [
        "WITH RECURSIVE r AS (SELECT 1 AS n UNION ALL SELECT n + 1 FROM r WHERE n < 3) "
        "SELECT * FROM r",
        "SELECT * FROM (WITH RECURSIVE r AS (SELECT 1 AS n) SELECT * FROM r) AS s",
    ],
)
def test_recursive_part_rejected(sql: str) -> None:
    with pytest.raises(RenderError, match="RECURSIVE"):
        compose([part("r", sql)], "SELECT * FROM r", Dialect.DUCKDB)


def test_unparseable_part_is_render_error() -> None:
    with pytest.raises(RenderError, match="part 'bad'"):
        compose([part("bad", "SELEC FROM WHERE (")], "SELECT * FROM bad", Dialect.DUCKDB)


# ── Final SELECT ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "select",
    [
        "INSERT INTO t SELECT * FROM rev",
        "UPDATE t SET a = 1",
        "DELETE FROM t",
        "CREATE TABLE t AS SELECT * FROM rev",
        "DROP TABLE t",
        "MERGE INTO t USING rev ON t.a = rev.account_id WHEN MATCHED THEN DELETE",
        "SELECT * FROM rev; SELECT 1",
        "SELECT * INTO t FROM rev",
        "WITH RECURSIVE r AS (SELECT 1 AS n) SELECT * FROM r, rev",
    ],
)
def test_final_select_must_be_one_read_only_query(select: str) -> None:
    with pytest.raises(RenderError):
        compose([REVENUE], select, Dialect.POSTGRES)


def test_data_modifying_cte_rejected() -> None:
    select = "WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM rev, d"
    with pytest.raises(RenderError, match="not read-only"):
        compose([REVENUE], select, Dialect.POSTGRES)


def test_final_select_cte_named_like_alias_rejected() -> None:
    with pytest.raises(RenderError, match="also a part alias"):
        compose([REVENUE], "WITH rev AS (SELECT 1) SELECT * FROM rev", Dialect.DUCKDB)


def test_final_select_ctes_are_merged_after_parts() -> None:
    result = compose(
        [REVENUE, HEADCOUNT],
        "WITH big AS (SELECT * FROM rev WHERE revenue > 100) "
        "SELECT big.account_id, hc.seats FROM big JOIN hc USING (account_id)",
        Dialect.DUCKDB,
    )
    assert cte_list(result.sql, Dialect.DUCKDB)[-3:] == ["hc__recent", "hc", "big"]
    assert result.raw_tables == []


def test_final_select_cte_renamed_when_a_part_reads_that_table() -> None:
    result = compose(
        [REVENUE],
        "WITH accounts AS (SELECT account_id FROM rev) SELECT * FROM accounts",
        Dialect.DUCKDB,
    )
    names = cte_list(result.sql, Dialect.DUCKDB)
    assert names[-1] == "accounts_2"
    tree = parse(result.sql, Dialect.DUCKDB)
    main_from = tree.args["from_"].this
    assert (main_from.name, main_from.alias) == ("accounts_2", "accounts")
    assert result.raw_tables == []


def test_raw_tables_are_listed_and_aliases_are_not() -> None:
    result = compose(
        [REVENUE],
        "SELECT r.account_id, fx.rate, c.name FROM rev AS r "
        "JOIN finance.fx AS fx ON fx.day = CURRENT_DATE "
        "LEFT JOIN customers AS c ON c.id = r.account_id",
        Dialect.DUCKDB,
    )
    assert result.raw_tables == ["customers", "finance.fx"]


def test_union_final_select() -> None:
    result = compose(
        [REVENUE, HEADCOUNT],
        "SELECT account_id FROM rev UNION SELECT account_id FROM hc",
        Dialect.DUCKDB,
    )
    tree = parse(result.sql, Dialect.DUCKDB)
    assert isinstance(tree, exp.Union)
    assert cte_list(result.sql, Dialect.DUCKDB)[-1] == "hc"
    assert not [i for i in result.issues if i.code is IssueCode.UNUSED_ALIAS]


def test_alias_reference_case_is_normalized() -> None:
    result = compose([REVENUE], 'SELECT "REV".account_id FROM "REV"', Dialect.DUCKDB)
    tree = parse(result.sql, Dialect.DUCKDB)
    main_from = tree.args["from_"].this
    assert main_from.name == "rev"
    assert result.raw_tables == []
    assert result.issues == []


# ── Dialects, determinism, execution ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "dialect", [Dialect.DUCKDB, Dialect.SNOWFLAKE, Dialect.BIGQUERY, Dialect.POSTGRES]
)
def test_output_parses_in_dialect_and_is_deterministic(dialect: Dialect) -> None:
    parts = [
        part(
            "rev",
            "WITH recent AS (SELECT account_id, amount FROM deals WHERE amount > 0) "
            "SELECT account_id, SUM(amount) AS revenue FROM recent GROUP BY account_id",
            dialect=dialect,
            grain="one row per account",
            join_keys=["account_id"],
        ),
        part(
            "hc",
            "WITH recent AS (SELECT account_id FROM seats) "
            "SELECT account_id, COUNT(*) AS seats FROM recent GROUP BY account_id",
            dialect=dialect,
            grain="one row per account",
            join_keys=["account_id"],
        ),
    ]
    select = (
        "SELECT r.account_id, r.revenue, h.seats FROM rev AS r "
        "LEFT JOIN hc AS h ON h.account_id = r.account_id"
    )
    first = compose(parts, select, dialect)
    second = compose(parts, select, dialect)
    assert first == second
    assert first.dialect is dialect
    assert cte_list(first.sql, dialect) == ["rev__recent", "rev", "hc__recent", "hc"]
    assert unresolved_tables(first.sql, dialect) == {"deals", "seats"}
    assert first.joins[0].known is True
    assert first.issues == []
    # Re-generating the parsed output is stable (pretty output is canonical).
    assert parse(first.sql, dialect).sql(dialect=str(dialect), pretty=True) == first.sql


def test_composed_query_runs_on_duckdb() -> None:
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE deals AS SELECT * FROM (VALUES (1, 100), (1, 50), (2, 70), (3, -5)) "
        "AS t(account_id, amount)"
    )
    con.execute(
        "CREATE TABLE seats AS SELECT * FROM (VALUES (1, 'a'), (1, 'b'), (2, 'c')) "
        "AS t(account_id, user_id)"
    )
    rev = part(
        "rev",
        "WITH recent AS (SELECT account_id, amount FROM deals WHERE amount > 0), "
        "totals AS (SELECT account_id, SUM(amount) AS revenue FROM recent GROUP BY 1) "
        "SELECT totals.account_id, totals.revenue FROM totals "
        "WHERE totals.account_id IN (SELECT account_id FROM recent)",
        grain="one row per account",
        join_keys=["account_id"],
    )
    hc = part(
        "hc",
        "WITH recent AS (SELECT account_id, user_id FROM seats) "
        "SELECT account_id, COUNT(*) AS seats FROM recent GROUP BY account_id",
        grain="one row per account",
        join_keys=["account_id"],
    )
    result: ComposedQuery = compose(
        [rev, hc],
        "WITH joined AS (SELECT r.account_id, r.revenue, h.seats FROM rev AS r "
        "JOIN hc AS h ON h.account_id = r.account_id) "
        "SELECT account_id, revenue / seats AS revenue_per_seat FROM joined ORDER BY account_id",
        Dialect.DUCKDB,
    )
    assert result.issues == []
    assert con.execute(result.sql).fetchall() == [(1, 75.0), (2, 70.0)]


# ── review fixes ────────────────────────────────────────────────────────────────────


def test_nested_cte_body_reads_the_hoisted_outer_cte() -> None:
    sql = (
        "WITH recent AS (SELECT 1 AS x) "
        "SELECT * FROM (WITH recent AS (SELECT x + 1 AS x FROM recent) SELECT * FROM recent) s"
    )
    assert duckdb.sql(sql).fetchall() == [(2,)]
    result = compose([part("x", sql)], "SELECT * FROM x", Dialect.DUCKDB)
    assert duckdb.sql(result.sql).fetchall() == [(2,)]


def test_ctes_differing_only_in_case_are_rejected() -> None:
    p = part(
        "x",
        'WITH "Recent" AS (SELECT 1 AS a), recent AS (SELECT 2 AS a) '
        'SELECT "Recent".a, recent.a FROM "Recent" JOIN recent ON TRUE',
        dialect=Dialect.SNOWFLAKE,
    )
    with pytest.raises(RenderError, match="differ only in case"):
        compose([p], "SELECT * FROM x", Dialect.SNOWFLAKE)


def test_final_select_ctes_differing_only_in_case_are_rejected() -> None:
    with pytest.raises(RenderError, match="differ only in case"):
        compose(
            [REVENUE],
            'WITH "A" AS (SELECT 1 AS v), a AS (SELECT 2 AS v) SELECT * FROM rev, "A", a',
            Dialect.SNOWFLAKE,
        )


@pytest.mark.parametrize(
    "select",
    [
        "SELECT pg_terminate_backend(123) FROM rev",
        "SELECT set_config('a', 'b', false) FROM rev",
        "SELECT nextval('s') FROM rev",
        "SELECT pg_read_file('/etc/passwd') FROM rev",
        "SELECT * FROM rev, dblink('host=x', 'DELETE FROM t') AS t(a INT)",
    ],
)
def test_side_effect_functions_are_not_read_only(select: str) -> None:
    rev = part("rev", "SELECT 1 AS a", dialect=Dialect.POSTGRES)
    with pytest.raises(RenderError, match="read-only"):
        compose([rev], select, Dialect.POSTGRES)


def test_side_effect_function_in_a_part_is_rejected() -> None:
    p = part("x", "SELECT SYSTEM$CANCEL_QUERY('q') AS r", dialect=Dialect.SNOWFLAKE)
    with pytest.raises(RenderError, match="system\\$cancel_query"):
        compose([p], "SELECT * FROM x", Dialect.SNOWFLAKE)


def test_duckdb_getenv_is_rejected() -> None:
    with pytest.raises(RenderError, match="getenv"):
        compose([REVENUE], "SELECT getenv('HOME') FROM rev", Dialect.DUCKDB)
