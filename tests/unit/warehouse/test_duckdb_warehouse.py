from pathlib import Path

import duckdb
import pytest

from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import WarehouseError
from cteforge.warehouse.duckdb import DuckDBWarehouse


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "wh.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE Accounts (Account_ID INTEGER, name VARCHAR)")
    con.execute("CREATE SCHEMA Sales")
    con.execute("CREATE TABLE Sales.Deals (deal_id INTEGER, Amount DECIMAL(12,2))")
    con.execute("INSERT INTO Accounts SELECT range, 'a' || range FROM range(5)")
    con.close()
    return path


@pytest.fixture()
def wh(db_path: Path):
    warehouse = DuckDBWarehouse(db_path)
    yield warehouse
    warehouse.close()


@pytest.mark.parametrize("name", ["accounts", "ACCOUNTS", "main.Accounts", "wh.main.accounts"])
def test_column_types_unquoted_any_case(wh, name):
    assert wh.column_types(name) == {"account_id": "INTEGER", "name": "VARCHAR"}


@pytest.mark.parametrize("name", ["sales.deals", "SALES.DEALS", "Sales.Deals"])
def test_column_types_schema_qualified(wh, name):
    assert wh.column_types(name) == {"deal_id": "INTEGER", "amount": "DECIMAL(12,2)"}


def test_unqualified_name_resolves_in_current_schema_only(wh):
    assert wh.column_types("deals") == {}


@pytest.mark.parametrize(
    "name", ["missing", "other.accounts", "a..b", "accounts' OR '1'='1", "x.y.z.w"]
)
def test_column_types_unknown_or_malformed_is_empty(wh, name):
    assert wh.column_types(name) == {}


def test_run_enforces_limit_and_truncation(wh):
    result = wh.run("SELECT account_id AS n FROM accounts ORDER BY 1", limit=3)
    assert result.columns == ["n"]
    assert result.rows == [(0,), (1,), (2,)]
    assert result.truncated is True
    full = wh.run("SELECT account_id FROM accounts", limit=5)
    assert len(full.rows) == 5 and full.truncated is False


def test_profile_max_rows_caps_requested_limit(db_path):
    profile = WarehouseProfile(
        name="local", dialect=Dialect.DUCKDB, options={"path": str(db_path)}, max_rows=2
    )
    wh = DuckDBWarehouse.from_profile(profile)
    try:
        result = wh.run("SELECT * FROM accounts", limit=100)
        assert len(result.rows) == 2 and result.truncated is True
        assert wh.dialect is Dialect.DUCKDB
    finally:
        wh.close()


def test_from_profile_requires_path():
    profile = WarehouseProfile(name="local", dialect=Dialect.DUCKDB)
    with pytest.raises(WarehouseError, match="options.path"):
        DuckDBWarehouse.from_profile(profile)


def test_connection_is_read_only(wh):
    with pytest.raises(WarehouseError):
        wh.run("INSERT INTO accounts VALUES (99, 'x')", limit=1)


def test_explain_raises_on_bad_sql(wh):
    wh.explain("SELECT * FROM accounts")
    with pytest.raises(WarehouseError, match="does not compile"):
        wh.explain("SELECT * FROM nope")


def test_missing_file(tmp_path):
    with pytest.raises(WarehouseError, match="not found"):
        DuckDBWarehouse(tmp_path / "absent.duckdb")


def test_explain_does_not_run_trailing_statements(wh, tmp_path):
    target = tmp_path / "leak.csv"
    with pytest.raises(WarehouseError, match="single SELECT"):
        wh.explain(f"SELECT 1; COPY (SELECT 42 AS v) TO '{target}'")
    assert not target.exists()


@pytest.mark.parametrize(
    "sql",
    [
        "COPY (SELECT 42) TO '{dir}/leak2.csv'",
        "CREATE TEMP TABLE accounts AS SELECT 'x' AS evil",
        "SELECT 1; SELECT 2",
        "SET threads = 1",
    ],
)
def test_run_refuses_anything_but_one_select(wh, tmp_path, sql):
    with pytest.raises(WarehouseError, match="single SELECT"):
        wh.run(sql.format(dir=tmp_path), limit=5)
    assert list(tmp_path.glob("leak*")) == []
    assert wh.column_types("accounts") == {"account_id": "INTEGER", "name": "VARCHAR"}


def test_run_cannot_read_files_outside_the_database(wh, tmp_path):
    outside = tmp_path / "outside.csv"
    outside.write_text("secret\nvalue\n")
    with pytest.raises(WarehouseError, match="Query failed"):
        wh.run(f"SELECT * FROM read_csv('{outside}')", limit=5)


@pytest.mark.parametrize(
    "sql",
    ["SELECT 1;", "WITH x AS (SELECT 1 AS a) SELECT a FROM x", "-- note\nFROM accounts"],
)
def test_single_selects_still_run_and_explain(wh, sql):
    wh.explain(sql)
    assert wh.run(sql, limit=1).rows
