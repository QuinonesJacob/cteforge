"""Redshift connector (ADR 0022) against a fake `redshift_connector` module.

The fake mirrors what the real driver does that matters here: `connect(**kwargs)`, an
`autocommit` attribute (off by default: the driver would then open its own read-write
`begin transaction`), `cursor.execute(sql, args)` with no other options, `rollback()`
only while a transaction is open, and server errors raised with the ErrorResponse fields
as one dict argument (S, C, M, F, L, R, ...).
"""

import logging
import sys
from types import ModuleType

import pytest

from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import WarehouseError
from cteforge.warehouse.redshift import (
    BEGIN_READ_ONLY_SQL,
    CURRENT_SCHEMA_SQL,
    STATEMENT_TIMEOUT_SQL,
    RedshiftWarehouse,
)

SECRET = "rs-hunter2-very-secret"
HOST = "analytics.abc123xyz.us-east-1.redshift.amazonaws.com"
SERVER_PATH = "/home/ec2-user/padb/src/pg/src/backend/parser/parse_relation.c"


def server_error(message: str, code: str = "42P01") -> Exception:
    """How redshift_connector raises a server error: ProgrammingError(dict of fields)."""
    return ProgrammingError(
        {"S": "ERROR", "C": code, "M": message, "F": SERVER_PATH, "L": "1251", "R": "rel"}
    )


class ProgrammingError(Exception):
    pass


class FakeCursor:
    def __init__(self, con: "FakeConnection") -> None:
        self.con = con
        self.description: list[tuple[str]] | None = None
        self._rows: list[tuple] = []

    def execute(self, operation, args=None):  # the real signature takes no `prepare=`
        con = self.con
        if not con.in_transaction and not con.autocommit:
            con.executed.append(("begin transaction", None))  # read-write, by the driver
            con.in_transaction = True
        con.executed.append((operation, args))
        if operation == BEGIN_READ_ONLY_SQL:
            con.in_transaction = True
        if con.fail_on is not None and operation.startswith(con.fail_on[0]):
            raise con.fail_on[1]
        self.description, self._rows = con.respond(operation)
        return self

    def fetchall(self):
        return tuple(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchmany(self, num=None):
        self.con.fetchmany_sizes.append(num)
        return tuple(self._rows[:num])

    def close(self):
        self.con.cursors_closed += 1


class FakeConnection:
    def __init__(self, kwargs: dict) -> None:
        self.kwargs = kwargs
        self.autocommit = False
        self.in_transaction = False
        self.executed: list[tuple] = []
        self.fetchmany_sizes: list[int | None] = []
        self.responses: dict[str, tuple] = {}
        self.fail_on: tuple[str, Exception] | None = None
        self.rollbacks = 0
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
        if not self.in_transaction:
            return
        self.executed.append(("rollback", None))
        self.in_transaction = False
        self.rollbacks += 1

    def close(self):
        self.closed = True


@pytest.fixture()
def rs(monkeypatch):
    module = ModuleType("redshift_connector")
    module.connections = []
    module.connect_error = None

    def connect(**kwargs):
        if module.connect_error is not None:
            raise module.connect_error
        con = FakeConnection(kwargs)
        module.connections.append(con)
        return con

    module.connect = connect
    monkeypatch.setitem(sys.modules, "redshift_connector", module)
    monkeypatch.setenv("RS_PASSWORD", SECRET)
    return module


def rs_profile(**kwargs) -> WarehouseProfile:
    base = {
        "name": "rs",
        "dialect": Dialect.REDSHIFT,
        "options": {"host": HOST, "port": 5439, "database": "analytics", "user": "cteforge_ro"},
        "secrets_env": {"password": "RS_PASSWORD"},
    }
    return WarehouseProfile(**(base | kwargs))


def statements(con: FakeConnection) -> list[str]:
    return [sql for sql, _ in con.executed]


# --- connecting and secrets -----------------------------------------------------------


def test_connect_kwargs_secret_from_env_and_autocommit_on(rs):
    RedshiftWarehouse(rs_profile(options={"host": HOST, "dbname": "analytics", "user": "ro"}))
    con = rs.connections[0]
    assert con.kwargs == {"host": HOST, "database": "analytics", "user": "ro", "password": SECRET}
    # So the driver never opens its own read-write "begin transaction".
    assert con.autocommit is True


def test_missing_driver_gives_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "redshift_connector", None)
    with pytest.raises(WarehouseError, match=r"cteforge\[redshift\]"):
        RedshiftWarehouse(rs_profile())


def test_plaintext_password_option_refused_before_connect(rs):
    profile = rs_profile(options={"host": HOST, "password": "plaintextpw"}, secrets_env={})
    with pytest.raises(WarehouseError, match="secrets_env") as err:
        RedshiftWarehouse(profile)
    assert "plaintextpw" not in str(err.value)
    assert rs.connections == []


@pytest.mark.parametrize(
    "options",
    [
        {"host": HOST, "iam": True},  # boto3 would read AWS credentials on its own
        {"host": HOST, "credentials_provider": "OktaCredentialsProvider"},
        {"host": HOST, "profile": "default"},
        {"host": HOST, "ssl_insecure": True},
        {"host": HOST, "autocommit": False},
    ],
)
def test_options_outside_the_allowlist_refused_by_name(rs, options):
    with pytest.raises(WarehouseError, match="does not accept") as err:
        RedshiftWarehouse(rs_profile(options=options))
    assert HOST not in str(err.value)
    assert rs.connections == []


def test_only_the_password_secret_is_accepted(rs, monkeypatch):
    monkeypatch.setenv("AWS_SECRET", "aws-secret-value")
    profile = rs_profile(secrets_env={"password": "RS_PASSWORD", "secret_access_key": "AWS_SECRET"})
    with pytest.raises(WarehouseError, match="only 'password'") as err:
        RedshiftWarehouse(profile)
    assert "aws-secret-value" not in str(err.value)
    assert rs.connections == []


def test_missing_env_var_names_the_var_only(rs, monkeypatch):
    monkeypatch.delenv("RS_PASSWORD")
    with pytest.raises(WarehouseError, match="RS_PASSWORD") as err:
        RedshiftWarehouse(rs_profile())
    assert SECRET not in str(err.value)
    assert rs.connections == []


def test_connect_error_is_scrubbed(rs):
    rs.connect_error = server_error(
        f'password authentication failed for user "cteforge_ro" at {HOST}:5439 ({SECRET})',
        code="28000",
    )
    with pytest.raises(WarehouseError) as err:
        RedshiftWarehouse(rs_profile())
    message = str(err.value)
    assert message.startswith("Could not connect to Redshift: password authentication failed")
    for leaked in (SECRET, HOST, "cteforge_ro", SERVER_PATH, "padb", "5439"):
        assert leaked not in message


def test_socket_connect_error_hides_ip(rs):
    rs.connect_error = OSError("communication error: [Errno 61] refused 10.1.2.3 port 5439")
    with pytest.raises(WarehouseError) as err:
        RedshiftWarehouse(rs_profile())
    assert "10.1.2.3" not in str(err.value) and "5439" not in str(err.value)


def test_driver_debug_logging_is_raised_to_warning_unless_set(rs):
    logger = logging.getLogger("redshift_connector")
    previous = logger.level
    try:
        logger.setLevel(logging.NOTSET)
        RedshiftWarehouse(rs_profile())
        assert logger.level == logging.WARNING
        logger.setLevel(logging.DEBUG)  # an operator's explicit choice is kept
        RedshiftWarehouse(rs_profile())
        assert logger.level == logging.DEBUG
    finally:
        logger.setLevel(previous)


# --- read-only transactions, single statement, timeout --------------------------------


def test_every_call_is_its_own_read_only_transaction_with_timeout(rs):
    wh = RedshiftWarehouse(rs_profile(statement_timeout_s=5))
    con = rs.connections[0]
    wh.run("SELECT 1", limit=1)
    wh.explain("SELECT 1")
    assert con.executed == [
        (BEGIN_READ_ONLY_SQL, None),
        (STATEMENT_TIMEOUT_SQL, ["5000"]),
        ("SELECT 1", None),
        ("rollback", None),
        (BEGIN_READ_ONLY_SQL, None),
        (STATEMENT_TIMEOUT_SQL, ["5000"]),
        ("EXPLAIN SELECT 1", None),
        ("rollback", None),
    ]
    assert "begin transaction" not in statements(con)  # never a read-write transaction


def test_timeout_off_still_read_only(rs):
    wh = RedshiftWarehouse(rs_profile(statement_timeout_s=None))
    wh.run("SELECT 1", limit=1)
    assert statements(rs.connections[0]) == [BEGIN_READ_ONLY_SQL, "SELECT 1", "rollback"]


def test_failed_statement_is_rolled_back_and_scrubbed(rs):
    wh = RedshiftWarehouse(rs_profile())
    con = rs.connections[0]
    con.fail_on = ("SELECT", server_error(f'relation "{SECRET}" does not exist'))
    with pytest.raises(WarehouseError) as err:
        wh.run("SELECT * FROM nope", limit=1)
    message = str(err.value)
    assert message == 'Query failed: relation "***" does not exist'
    assert SERVER_PATH not in message and "'C'" not in message
    assert err.value.__cause__ is None and err.value.__suppress_context__
    assert statements(con)[-1] == "rollback"


def test_cancelled_statement_reports_timeout_from_sqlstate(rs):
    wh = RedshiftWarehouse(rs_profile(statement_timeout_s=2))
    rs.connections[0].fail_on = ("SELECT", server_error("canceling statement", code="57014"))
    with pytest.raises(WarehouseError, match=r"^query timed out after 2s$"):
        wh.run("SELECT 1", limit=1)


def test_cancelled_statement_reports_timeout_from_redshift_message(rs):
    # AWS docs (statement_timeout): "ERROR:  Query (150) canceled on user's request".
    wh = RedshiftWarehouse(rs_profile(statement_timeout_s=3))
    rs.connections[0].fail_on = (
        "SELECT",
        server_error("Query (150) cancelled on user's request", code="XX000"),
    )
    with pytest.raises(WarehouseError, match=r"^query timed out after 3s$"):
        wh.run("SELECT 1", limit=1)


def test_other_errors_are_not_timeouts(rs):
    wh = RedshiftWarehouse(rs_profile(statement_timeout_s=2))
    rs.connections[0].fail_on = ("SELECT", server_error("division by zero", code="22012"))
    with pytest.raises(WarehouseError, match="^Query failed: division by zero$"):
        wh.run("SELECT 1", limit=1)


def test_multi_statement_string_is_sent_as_one_statement_and_refused(rs):
    # The driver sends it as one Parse (extended protocol); Redshift refuses it.
    wh = RedshiftWarehouse(rs_profile())
    con = rs.connections[0]
    sql = "SELECT 1; DELETE FROM t"
    con.fail_on = (sql, server_error("cannot insert multiple commands into a prepared statement"))
    with pytest.raises(WarehouseError, match="cannot insert multiple commands"):
        wh.run(sql, limit=1)
    assert [s for s in statements(con) if s.startswith("SELECT 1")] == [sql]


# --- schema lookup ----------------------------------------------------------------------


def test_column_types_use_svv_columns_with_parameters(rs):
    wh = RedshiftWarehouse(rs_profile(statement_timeout_s=None))
    con = rs.connections[0]
    con.responses["SELECT lower(column_name)"] = (
        [("column_name",), ("data_type",)],
        [("deal_id", "integer"), ("payload", "super"), ("closed_at", "timestamp")],
    )
    types = wh.column_types("Spectrum_Ext.Deals_Secret_Name")
    assert types == {"deal_id": "integer", "payload": "super", "closed_at": "timestamp"}
    sql, params = con.executed[-2]  # last is the rollback
    assert "FROM svv_columns" in sql
    assert "Deals_Secret_Name" not in sql and "Spectrum_Ext" not in sql
    assert params == ["Deals_Secret_Name", "Spectrum_Ext"]
    assert statements(con) == [BEGIN_READ_ONLY_SQL, sql, "rollback"]


def test_unqualified_name_resolves_current_schema_first(rs):
    wh = RedshiftWarehouse(rs_profile(statement_timeout_s=None))
    con = rs.connections[0]
    con.responses[CURRENT_SCHEMA_SQL] = ([("current_schema",)], [("analytics",)])
    con.responses["SELECT lower(column_name)"] = ([("c",), ("t",)], [("id", "bigint")])
    assert wh.column_types("deals") == {"id": "bigint"}
    lookup_sql, params = con.executed[-2]
    assert statements(con)[:2] == [BEGIN_READ_ONLY_SQL, CURRENT_SCHEMA_SQL]
    assert params == ["deals", "analytics"]
    assert "current_schema" not in lookup_sql


def test_unqualified_name_without_current_schema_is_unknown(rs):
    wh = RedshiftWarehouse(rs_profile(statement_timeout_s=None))
    con = rs.connections[0]
    con.responses[CURRENT_SCHEMA_SQL] = ([("current_schema",)], [(None,)])
    assert wh.column_types("deals") == {}
    assert statements(con) == [BEGIN_READ_ONLY_SQL, CURRENT_SCHEMA_SQL, "rollback"]


def test_three_part_name_filters_catalog(rs):
    wh = RedshiftWarehouse(rs_profile(statement_timeout_s=None))
    con = rs.connections[0]
    wh.column_types("dev.public.t")
    _, params = con.executed[-2]
    assert params == ["t", "public", "dev"]


def test_malformed_name_skips_query(rs):
    wh = RedshiftWarehouse(rs_profile())
    assert wh.column_types("a.b.c.d") == {}
    assert rs.connections[0].executed == []


# --- run, explain, close ------------------------------------------------------------------


def test_run_limit_and_truncation(rs):
    wh = RedshiftWarehouse(rs_profile(max_rows=4, statement_timeout_s=None))
    con = rs.connections[0]
    con.responses["SELECT"] = ([("n",)], [(i,) for i in range(10)])
    small = wh.run("SELECT n", limit=3)
    assert small.rows == [(0,), (1,), (2,)] and small.truncated is True
    capped = wh.run("SELECT n", limit=1000)
    assert len(capped.rows) == 4 and capped.truncated is True
    assert con.fetchmany_sizes == [4, 5]
    con.responses["SELECT"] = ([("n",)], [(1,), (2,)])
    exact = wh.run("SELECT n", limit=2)
    assert exact.rows == [(1,), (2,)] and exact.truncated is False and exact.columns == ["n"]


def test_explain_failure_and_cursors_closed(rs):
    wh = RedshiftWarehouse(rs_profile())
    con = rs.connections[0]
    wh.explain("SELECT 1")
    assert con.cursors_closed == 1
    con.fail_on = ("EXPLAIN", server_error('relation "nope" does not exist'))
    with pytest.raises(WarehouseError, match="^Query does not compile: relation"):
        wh.explain("SELECT * FROM nope")
    assert con.cursors_closed == 2


def test_close_and_dialect(rs):
    wh = RedshiftWarehouse(rs_profile())
    wh.close()
    assert rs.connections[0].closed is True
    assert wh.dialect is Dialect.REDSHIFT


def test_connections_yml_redshift_profile_opens_this_connector(rs):
    from cteforge.files.connections import profiles_from_yaml
    from cteforge.services.connections import open_profile

    (profile,) = profiles_from_yaml(
        "profiles:\n  wh:\n    dialect: redshift\n"
        f"    options: {{host: {HOST}, port: 5439, database: analytics, user: ro}}\n"
        "    secrets_env: {password: RS_PASSWORD}\n"
    )
    assert profile.dialect is Dialect.REDSHIFT
    wh = open_profile(profile)
    assert isinstance(wh, RedshiftWarehouse)
    assert rs.connections[0].kwargs["password"] == SECRET
