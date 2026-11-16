"""Postgres, Snowflake and Databricks connectors against fake driver modules."""

import sys
from types import ModuleType

import pytest

from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import WarehouseError
from cteforge.warehouse.databricks import DatabricksWarehouse
from cteforge.warehouse.postgres import STATEMENT_TIMEOUT_SQL, PostgresWarehouse
from cteforge.warehouse.snowflake import SnowflakeWarehouse

SECRET = "hunter2-very-secret"


class FakeCursor:
    def __init__(self, con: "FakeConnection") -> None:
        self.con = con
        self.description: list[tuple[str]] | None = None
        self._rows: list[tuple] = []

    def execute(self, sql, params=None, **options):
        self.con.executed.append((sql, params))
        self.con.execute_options.append(options)
        if self.con.fail_with is not None:
            raise self.con.fail_with
        self.description, self._rows = self.con.respond(sql)

    def columns(self, **kwargs):
        self.con.metadata_calls.append(kwargs)
        self.description, self._rows = self.con.columns_result

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0]

    def fetchmany(self, size):
        self.con.fetchmany_sizes.append(size)
        return self._rows[:size]

    def close(self):
        self.con.cursors_closed += 1


class FakeConnection:
    def __init__(self, kwargs: dict) -> None:
        self.kwargs = kwargs
        self.executed: list[tuple] = []
        self.execute_options: list[dict] = []
        self.read_only: bool | None = None
        self.rollbacks = 0
        self.metadata_calls: list[dict] = []
        self.fetchmany_sizes: list[int] = []
        self.responses: dict[str, tuple] = {}
        self.columns_result: tuple = ([], [])
        self.fail_with: Exception | None = None
        self.cursors_closed = 0
        self.closed = False

    def respond(self, sql: str) -> tuple:
        for prefix, response in self.responses.items():
            if sql.startswith(prefix):
                return response
        return [("x",)], []

    def cursor(self):
        return FakeCursor(self)

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def fake_driver(monkeypatch, module_name: str) -> ModuleType:
    module = ModuleType(module_name)
    module.connections = []
    module.connect_error = None

    def connect(**kwargs):
        if module.connect_error is not None:
            raise module.connect_error
        con = FakeConnection(kwargs)
        module.connections.append(con)
        return con

    module.connect = connect
    monkeypatch.setitem(sys.modules, module_name, module)
    return module


def pg_profile(**kwargs) -> WarehouseProfile:
    base = {
        "name": "pg",
        "dialect": Dialect.POSTGRES,
        "options": {"host": "db.internal.example", "database": "analytics", "user": "ro"},
        "secrets_env": {"password": "PG_PASSWORD"},
    }
    return WarehouseProfile(**(base | kwargs))


@pytest.fixture()
def pg(monkeypatch):
    driver = fake_driver(monkeypatch, "psycopg")
    monkeypatch.setenv("PG_PASSWORD", SECRET)
    return driver


# --- Postgres -------------------------------------------------------------------------


def test_postgres_secret_from_env_and_options(pg):
    PostgresWarehouse(pg_profile())
    con = pg.connections[0]
    assert con.kwargs["password"] == SECRET
    assert con.kwargs["dbname"] == "analytics" and "database" not in con.kwargs
    assert con.kwargs["autocommit"] is False


def test_postgres_every_call_is_its_own_read_only_transaction(pg):
    wh = PostgresWarehouse(pg_profile())
    con = pg.connections[0]
    assert con.read_only is True  # psycopg then opens each transaction BEGIN READ ONLY
    wh.run("SELECT 1", limit=1)
    wh.explain("SELECT 1")
    wh.column_types("t")
    statements = [sql for sql, _ in con.executed if sql != STATEMENT_TIMEOUT_SQL]
    assert statements[:2] == ["SELECT 1", "EXPLAIN SELECT 1"]
    assert con.rollbacks == 3  # session settings changed by a SELECT never carry over


def test_postgres_rolls_back_after_failed_statement(pg):
    wh = PostgresWarehouse(pg_profile())
    con = pg.connections[0]
    con.fail_with = RuntimeError("boom")
    with pytest.raises(WarehouseError):
        wh.run("SELECT 1", limit=1)
    assert con.rollbacks == 1


def test_postgres_profile_cannot_turn_on_autocommit(pg):
    PostgresWarehouse(pg_profile(options={"host": "h.example", "autocommit": True}))
    assert pg.connections[0].kwargs["autocommit"] is False


def test_postgres_statements_use_extended_protocol(pg):
    # prepare=True makes psycopg use Parse/Bind (extended protocol), which refuses
    # "EXPLAIN SELECT 1; SET ..." instead of running every statement in the string.
    wh = PostgresWarehouse(pg_profile())
    con = pg.connections[0]
    wh.explain("SELECT 1; SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE")
    wh.run("SELECT 1", limit=1)
    wh.column_types("t")
    assert con.execute_options == [{"prepare": True}] * 6  # 3 calls, each with its timeout


def test_postgres_plaintext_secret_option_refused(pg):
    profile = pg_profile(options={"host": "h.example", "password": "plaintextpw"}, secrets_env={})
    with pytest.raises(WarehouseError, match="secrets_env") as err:
        PostgresWarehouse(profile)
    assert "plaintextpw" not in str(err.value)
    assert pg.connections == []


def test_postgres_conninfo_option_refused(pg):
    profile = pg_profile(options={"conninfo": "postgresql://bob:pw123@db.corp:5432/prod"})
    with pytest.raises(WarehouseError, match="conninfo") as err:
        PostgresWarehouse(profile)
    assert "pw123" not in str(err.value) and "db.corp" not in str(err.value)
    assert pg.connections == []


def test_postgres_connect_error_hides_ips_ports_user_and_database(pg):
    pg.connect_error = RuntimeError(
        'connection to server at "127.0.0.1", port 1 failed: Connection refused\n'
        "multiple connection attempts failed. hostaddr: '::1' hostaddr: '127.0.0.1'\n"
        'FATAL: password authentication failed for user "bob"; database "proddb"'
    )
    options = {"host": "localhost", "port": 1, "user": "bob", "database": "proddb"}
    with pytest.raises(WarehouseError) as err:
        PostgresWarehouse(pg_profile(options=options))
    message = str(err.value)
    for leaked in ("127.0.0.1", "::1", "port 1", "bob", "proddb"):
        assert leaked not in message
    assert "Connection refused" in message and "password authentication failed" in message


def test_postgres_missing_env_var_names_var_without_values(monkeypatch, pg):
    monkeypatch.delenv("PG_SSLKEY_PW", raising=False)
    profile = pg_profile(secrets_env={"password": "PG_PASSWORD", "sslpassword": "PG_SSLKEY_PW"})
    with pytest.raises(WarehouseError) as err:
        PostgresWarehouse(profile)
    assert "PG_SSLKEY_PW" in str(err.value)
    assert SECRET not in str(err.value)
    assert pg.connections == []


def test_postgres_connect_error_is_scrubbed(pg):
    pg.connect_error = RuntimeError(
        f'connection to server at "db.internal.example" failed: password "{SECRET}" rejected '
        "(postgresql://ro@db.internal.example/analytics)"
    )
    with pytest.raises(WarehouseError) as err:
        PostgresWarehouse(pg_profile())
    message = str(err.value)
    assert SECRET not in message
    assert "db.internal.example" not in message
    assert "postgresql://" not in message
    assert message.startswith("Could not connect to Postgres")


def test_postgres_query_error_is_scrubbed_and_chain_dropped(pg):
    wh = PostgresWarehouse(pg_profile())
    pg.connections[0].fail_with = RuntimeError(f"syntax error near {SECRET}")
    with pytest.raises(WarehouseError) as err:
        wh.run("SELECT bad", limit=1)
    assert SECRET not in str(err.value)
    assert "syntax error near ***" in str(err.value)
    assert err.value.__cause__ is None and err.value.__suppress_context__


def test_postgres_column_types_passes_identifiers_as_parameters(pg):
    wh = PostgresWarehouse(pg_profile())
    con = pg.connections[0]
    con.responses["SELECT lower(column_name)"] = (
        [("column_name",), ("data_type",)],
        [("deal_id", "integer"), ("closed_at", "timestamp without time zone")],
    )
    types = wh.column_types("Finance.Deals_Secret_Name")
    assert types == {"deal_id": "integer", "closed_at": "timestamp without time zone"}
    sql, params = con.executed[-1]
    assert "Deals_Secret_Name" not in sql and "Finance" not in sql
    assert params == ["Deals_Secret_Name", "Finance"]


def test_postgres_column_types_malformed_name_skips_query(pg):
    wh = PostgresWarehouse(pg_profile())
    assert wh.column_types("a.b.c.d") == {}
    assert pg.connections[0].executed == []


def test_postgres_run_limit_and_truncation(pg):
    wh = PostgresWarehouse(pg_profile(max_rows=4))
    con = pg.connections[0]
    con.responses["SELECT"] = ([("n",)], [(i,) for i in range(10)])
    small = wh.run("SELECT n", limit=3)
    assert small.rows == [(0,), (1,), (2,)] and small.truncated is True
    capped = wh.run("SELECT n", limit=1000)
    assert len(capped.rows) == 4 and capped.truncated is True
    assert con.fetchmany_sizes == [4, 5]
    con.responses["SELECT"] = ([("n",)], [(1,), (2,)])
    exact = wh.run("SELECT n", limit=2)
    assert exact.rows == [(1,), (2,)] and exact.truncated is False
    assert exact.columns == ["n"]


def test_postgres_explain_and_cursors_closed(pg):
    wh = PostgresWarehouse(pg_profile())
    wh.explain("SELECT 1")
    con = pg.connections[0]
    assert con.executed[-1] == ("EXPLAIN SELECT 1", None)
    assert con.cursors_closed == 1
    con.fail_with = RuntimeError("relation does not exist")
    with pytest.raises(WarehouseError, match="does not compile"):
        wh.explain("SELECT * FROM nope")


def test_postgres_close(pg):
    wh = PostgresWarehouse(pg_profile())
    wh.close()
    assert pg.connections[0].closed is True
    assert wh.dialect is Dialect.POSTGRES


@pytest.mark.parametrize(
    ("module", "cls", "extra"),
    [
        ("psycopg", PostgresWarehouse, "postgres"),
        ("snowflake.connector", SnowflakeWarehouse, "snowflake"),
        ("databricks.sql", DatabricksWarehouse, "databricks"),
    ],
)
def test_missing_driver_gives_install_hint(monkeypatch, module, cls, extra):
    monkeypatch.setitem(sys.modules, module, None)
    with pytest.raises(WarehouseError, match=rf"cteforge\[{extra}\]"):
        cls(pg_profile(secrets_env={}))


# --- Snowflake ------------------------------------------------------------------------


@pytest.fixture()
def sf(monkeypatch):
    driver = fake_driver(monkeypatch, "snowflake.connector")
    monkeypatch.setenv("SF_PASSWORD", SECRET)
    return driver


def sf_profile(**kwargs) -> WarehouseProfile:
    base = {
        "name": "sf",
        "dialect": Dialect.SNOWFLAKE,
        "options": {"account": "acme-xy123", "user": "CTEFORGE", "role": "CTEFORGE_RO"},
        "secrets_env": {"password": "SF_PASSWORD"},
    }
    return WarehouseProfile(**(base | kwargs))


def test_snowflake_connect_kwargs_and_secret(sf):
    SnowflakeWarehouse(sf_profile())
    kwargs = sf.connections[0].kwargs
    assert kwargs == {
        "account": "acme-xy123",
        "user": "CTEFORGE",
        "role": "CTEFORGE_RO",
        "password": SECRET,
        "session_parameters": {"STATEMENT_TIMEOUT_IN_SECONDS": 60},  # profile default
    }


def test_snowflake_explain_uses_explain_statement(sf):
    wh = SnowflakeWarehouse(sf_profile())
    wh.explain("SELECT 1")
    assert sf.connections[0].executed[-1] == ("EXPLAIN USING TEXT SELECT 1", None)


def test_snowflake_every_execute_allows_exactly_one_statement(sf):
    wh = SnowflakeWarehouse(sf_profile())
    wh.explain("SELECT 1")
    wh.run("SELECT 1", limit=1)
    wh.column_types("t")
    assert sf.connections[0].execute_options == [{"num_statements": 1}] * 3


def test_snowflake_private_key_password_option_refused(sf):
    options = {"account": "acme-xy123", "private_key_file_pwd": "keypw-123"}
    with pytest.raises(WarehouseError, match="private_key_file_pwd") as err:
        SnowflakeWarehouse(sf_profile(options=options))
    assert "keypw-123" not in str(err.value)
    assert sf.connections == []


def test_snowflake_column_types_parameterized(sf):
    wh = SnowflakeWarehouse(sf_profile())
    con = sf.connections[0]
    con.responses["SELECT lower(column_name)"] = (
        [("c",), ("t",)],
        [("amount", "NUMBER"), ("closed_at", "TIMESTAMP_NTZ")],
    )
    assert wh.column_types("ORDERS_TABLE") == {"amount": "NUMBER", "closed_at": "TIMESTAMP_NTZ"}
    sql, params = con.executed[-1]
    assert "ORDERS_TABLE" not in sql and params == ["ORDERS_TABLE"]
    assert "CURRENT_SCHEMA()" in sql


def test_snowflake_error_scrubs_secret_and_account(sf):
    sf.connect_error = RuntimeError(f"250001: Could not connect acme-xy123 with {SECRET}")
    with pytest.raises(WarehouseError) as err:
        SnowflakeWarehouse(sf_profile())
    assert SECRET not in str(err.value) and "acme-xy123" not in str(err.value)


def test_snowflake_run_truncates(sf):
    wh = SnowflakeWarehouse(sf_profile())
    sf.connections[0].responses["SELECT"] = ([("N",)], [(1,), (2,), (3,)])
    result = wh.run("SELECT n", limit=2)
    assert result.rows == [(1,), (2,)] and result.truncated is True
    assert result.columns == ["N"]


# --- Databricks -----------------------------------------------------------------------

COLUMN_DESCRIPTION = [
    ("TABLE_CAT",),
    ("TABLE_SCHEM",),
    ("TABLE_NAME",),
    ("COLUMN_NAME",),
    ("DATA_TYPE",),
    ("TYPE_NAME",),
]


@pytest.fixture()
def dbx(monkeypatch):
    driver = fake_driver(monkeypatch, "databricks.sql")
    monkeypatch.setenv("DATABRICKS_TOKEN", SECRET)
    return driver


def dbx_profile() -> WarehouseProfile:
    return WarehouseProfile(
        name="dbx",
        dialect=Dialect.DATABRICKS,
        options={"server_hostname": "adb-123.azuredatabricks.net", "http_path": "/sql/1.0/wh/1"},
        secrets_env={"access_token": "DATABRICKS_TOKEN"},
    )


def test_databricks_token_from_env(dbx):
    DatabricksWarehouse(dbx_profile())
    assert dbx.connections[0].kwargs["access_token"] == SECRET


def test_databricks_plaintext_token_option_refused_before_connect(dbx):
    dbx.connect_error = RuntimeError("Invalid access token dapiPLAINTEXT123 rejected")
    profile = WarehouseProfile(
        name="dbx",
        dialect=Dialect.DATABRICKS,
        options={"server_hostname": "x.cloud", "http_path": "/sql/1", "access_token": "dapi1"},
    )
    with pytest.raises(WarehouseError, match="access_token") as err:
        DatabricksWarehouse(profile)
    assert "dapi1" not in str(err.value)
    assert "Could not connect" not in str(err.value)


def test_databricks_column_types_uses_metadata_api_and_exact_match(dbx):
    wh = DatabricksWarehouse(dbx_profile())
    con = dbx.connections[0]
    con.responses["SELECT current_catalog()"] = ([("c",), ("s",)], [("main", "sales")])
    con.columns_result = (
        COLUMN_DESCRIPTION,
        [
            ("main", "sales", "my_table", "ID", 4, "INT"),
            ("main", "sales", "my_table", "amount", 3, "DECIMAL(12,2)"),
            ("main", "sales", "myXtable", "other", 12, "STRING"),  # LIKE-pattern match
        ],
    )
    assert wh.column_types("My_Table") == {"id": "INT", "amount": "DECIMAL(12,2)"}
    assert con.metadata_calls == [
        {"catalog_name": "main", "schema_name": "sales", "table_name": "my_table"}
    ]
    assert all("my_table" not in sql.lower() for sql, _ in con.executed)


def test_databricks_qualified_name_skips_namespace_lookup(dbx):
    wh = DatabricksWarehouse(dbx_profile())
    con = dbx.connections[0]
    con.columns_result = (COLUMN_DESCRIPTION, [("cat", "sch", "t", "a", 4, "INT")])
    assert wh.column_types("Cat.Sch.T") == {"a": "INT"}
    assert con.executed == []
    assert con.metadata_calls[0] == {"catalog_name": "cat", "schema_name": "sch", "table_name": "t"}


def test_databricks_explain_detects_planning_error_text(dbx):
    wh = DatabricksWarehouse(dbx_profile())
    con = dbx.connections[0]
    con.responses["EXPLAIN"] = ([("plan",)], [("== Physical Plan ==\nScan",)])
    wh.explain("SELECT 1")
    con.responses["EXPLAIN"] = (
        [("plan",)],
        [("Error occurred during query planning: \n[TABLE_OR_VIEW_NOT_FOUND] nope",)],
    )
    with pytest.raises(WarehouseError, match="TABLE_OR_VIEW_NOT_FOUND"):
        wh.explain("SELECT * FROM nope")


def test_databricks_error_scrubs_token_and_host(dbx):
    wh = DatabricksWarehouse(dbx_profile())
    dbx.connections[0].fail_with = RuntimeError(
        f"Invalid token {SECRET} for https://adb-123.azuredatabricks.net/sql/1.0/wh/1"
    )
    with pytest.raises(WarehouseError) as err:
        wh.run("SELECT 1", limit=1)
    assert SECRET not in str(err.value)
    assert "adb-123" not in str(err.value)


# --- statement timeouts ---------------------------------------------------------------


class QueryCanceled(Exception):
    sqlstate = "57014"


def test_postgres_sets_transaction_local_timeout_before_every_statement(pg):
    wh = PostgresWarehouse(pg_profile(statement_timeout_s=5))
    con = pg.connections[0]
    wh.run("SELECT 1", limit=1)
    wh.explain("SELECT 1")
    assert con.executed == [
        (STATEMENT_TIMEOUT_SQL, ["5000"]),
        ("SELECT 1", None),
        (STATEMENT_TIMEOUT_SQL, ["5000"]),
        ("EXPLAIN SELECT 1", None),
    ]
    assert con.rollbacks == 2  # set_config(..., true) ends with each transaction


def test_postgres_timeout_off_sends_no_setting(pg):
    wh = PostgresWarehouse(pg_profile(statement_timeout_s=None))
    wh.run("SELECT 1", limit=1)
    assert pg.connections[0].executed == [("SELECT 1", None)]


def test_postgres_cancelled_statement_reports_timeout(pg):
    wh = PostgresWarehouse(pg_profile(statement_timeout_s=2))
    pg.connections[0].fail_with = QueryCanceled("canceling statement due to statement timeout")
    with pytest.raises(WarehouseError, match=r"^query timed out after 2s$"):
        wh.run("SELECT pg_sleep(10)", limit=1)


def test_snowflake_timeout_session_parameter_and_error(sf):
    wh = SnowflakeWarehouse(sf_profile(statement_timeout_s=9))
    con = sf.connections[0]
    assert con.kwargs["session_parameters"] == {"STATEMENT_TIMEOUT_IN_SECONDS": 9}
    con.fail_with = QueryCanceled("Statement reached its statement or warehouse timeout")
    with pytest.raises(WarehouseError, match=r"^query timed out after 9s$"):
        wh.run("SELECT 1", limit=1)


def test_snowflake_timeout_off(sf):
    SnowflakeWarehouse(sf_profile(statement_timeout_s=None))
    assert "session_parameters" not in sf.connections[0].kwargs


def test_databricks_timeout_session_configuration(dbx):
    DatabricksWarehouse(dbx_profile())
    assert dbx.connections[0].kwargs["session_configuration"] == {"STATEMENT_TIMEOUT": "60"}


def test_other_errors_are_not_reported_as_timeouts(pg):
    wh = PostgresWarehouse(pg_profile(statement_timeout_s=2))
    pg.connections[0].fail_with = RuntimeError("relation does not exist")
    with pytest.raises(WarehouseError, match="Query failed: relation does not exist"):
        wh.run("SELECT 1", limit=1)
