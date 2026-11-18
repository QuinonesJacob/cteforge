"""MySQL connector (ADR 0028) against a fake `pymysql` module.

The fake mirrors what PyMySQL 1.2.4 does that matters here: `connect(**kwargs)` (connects at
once), an `open` property and `connect()` to reopen, `cursor()` returning the connection's
`cursorclass` (the connector passes `pymysql.cursors.SSCursor`), `cursor.execute(query,
args=None)` that formats `%s` with escaped arguments only when `args` is given,
`fetchmany` / `fetchall` / `fetchone`, `cursor.close()` (an unbuffered cursor reads the
rest of its result), `rollback()`, and server errors raised as `OperationalError(code,
message)` / `ProgrammingError(code, message)`. What only a real server can show (READ ONLY
refusals, the multi-statement refusal, max_execution_time) is in
tests/integration/test_mysql_live.py.
"""

import ssl
import sys
from types import ModuleType

import pytest

from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import WarehouseError
from cteforge.warehouse.mysql import (
    COLUMNS_SQL,
    CURRENT_DATABASE_COLUMNS_SQL,
    SESSION_MODE_SQL,
    START_READ_ONLY_SQL,
    STATEMENT_TIMEOUT_SQL,
    MySQLWarehouse,
    error_message,
)

SECRET = "my-hunter2-very-secret"
HOST = "mysql-prod-1.internal.example.com"
USER = "cteforge_ro"
DEFAULT_MODE = (
    "ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,"
    "ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION"
)


class OperationalError(Exception):
    pass


class ProgrammingError(Exception):
    pass


class SSCursor:
    """Stands for pymysql.cursors.SSCursor (the class the connector must pass)."""


class FakeCursor:
    def __init__(self, con: "FakeConnection") -> None:
        self.con = con
        self.description: list[tuple[str]] | None = None
        self._rows: list[tuple] = []
        self._pos = 0

    def execute(self, query, args=None):
        con = self.con
        sent = query if args is None else query % tuple(_escape(a) for a in args)
        con.executed.append((query, args))
        con.sent.append(sent)
        if con.fail_on is not None and sent.startswith(con.fail_on[0]):
            raise con.fail_on[1]
        self.description, self._rows = con.respond(sent)
        self._pos = 0
        return 0

    def _take(self, n):
        rows = self._rows[self._pos : self._pos + n]
        self._pos += len(rows)
        self.con.rows_read += len(rows)
        return rows

    def fetchall(self):
        return self._take(len(self._rows))

    def fetchone(self):
        rows = self._take(1)
        return rows[0] if rows else None

    def fetchmany(self, size=None):
        self.con.fetchmany_sizes.append(size)
        return self._take(size)

    def close(self):
        con = self.con
        con.drained += len(self._rows) - self._pos  # SSCursor reads the rest of the result
        con.cursors_closed += 1
        if con.close_error is not None:
            raise con.close_error


def _escape(value):
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise TypeError(f"unexpected argument {value!r}")
    return str(value) if isinstance(value, int) else "'" + value.replace("'", "''") + "'"


class FakeConnection:
    def __init__(self, kwargs: dict) -> None:
        self.kwargs = kwargs
        self.open = True
        self.executed: list[tuple] = []
        self.sent: list[str] = []
        self.fetchmany_sizes: list[int | None] = []
        self.responses: dict[str, tuple] = {SESSION_MODE_SQL: ([("m",)], [(DEFAULT_MODE,)])}
        self.fail_on: tuple[str, Exception] | None = None
        self.close_error: Exception | None = None
        self.rows_read = 0
        self.drained = 0
        self.rollbacks = 0
        self.reconnects = 0
        self.cursors_closed = 0
        self.closed = False

    def respond(self, sql: str) -> tuple:
        for prefix, response in self.responses.items():
            if sql.startswith(prefix):
                return response
        return [("x",)], []

    def cursor(self):
        assert self.kwargs["cursorclass"] is SSCursor
        return FakeCursor(self)

    def connect(self):
        self.open = True
        self.reconnects += 1

    def rollback(self):
        self.sent.append("ROLLBACK")
        self.rollbacks += 1

    def close(self):
        if not self.open:
            raise ProgrammingError(0, "Already closed")
        self.open = False
        self.closed = True


@pytest.fixture()
def my(monkeypatch):
    module = ModuleType("pymysql")
    cursors = ModuleType("pymysql.cursors")
    cursors.SSCursor = SSCursor
    module.cursors = cursors
    module.connections = []
    module.connect_error = None
    module.mode = DEFAULT_MODE

    def connect(**kwargs):
        if module.connect_error is not None:
            raise module.connect_error
        con = FakeConnection(kwargs)
        con.responses[SESSION_MODE_SQL] = ([("m",)], [(module.mode,)])
        module.connections.append(con)
        return con

    module.connect = connect
    monkeypatch.setitem(sys.modules, "pymysql", module)
    monkeypatch.setitem(sys.modules, "pymysql.cursors", cursors)
    monkeypatch.setenv("MYSQL_PASSWORD", SECRET)
    return module


def my_profile(**kwargs) -> WarehouseProfile:
    options = {"host": HOST, "port": 3306, "database": "shop", "user": USER}
    base = {
        "name": "my",
        "dialect": Dialect.MYSQL,
        "options": options | kwargs.pop("extra", {}),
        "secrets_env": {"password": "MYSQL_PASSWORD"},
    }
    return WarehouseProfile(**(base | kwargs))


def query_log(con: FakeConnection) -> list[str]:
    """What reached the server after the connect-time session check."""
    return con.sent[con.sent.index("ROLLBACK") + 1 :]


# ── Connecting, options, secrets ────────────────────────────────────────────────────


def test_connect_kwargs_are_the_allowlist_plus_fixed_safe_values(my):
    MySQLWarehouse(my_profile())
    kwargs = dict(my.connections[0].kwargs)
    context = kwargs.pop("ssl")
    assert kwargs == {
        "host": HOST,
        "port": 3306,
        "database": "shop",
        "user": USER,
        "password": SECRET,
        "read_timeout": 90,  # statement timeout (60 s) + 30 s
        "charset": "utf8mb4",
        "autocommit": False,
        "local_infile": False,
        "client_flag": 0,
        "init_command": None,
        "sql_mode": None,
        "read_default_file": None,
        "cursorclass": SSCursor,
    }
    # Verified TLS by default: certificate and host name, TLS 1.2 or later.
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode is ssl.CERT_REQUIRED and context.check_hostname is True
    assert context.minimum_version is ssl.TLSVersion.TLSv1_2


def test_explicit_read_timeout_is_kept(my):
    MySQLWarehouse(my_profile(extra={"read_timeout": 7, "connect_timeout": 3}))
    kwargs = my.connections[0].kwargs
    assert kwargs["read_timeout"] == 7 and kwargs["connect_timeout"] == 3


def test_no_statement_timeout_means_no_default_read_timeout(my):
    MySQLWarehouse(my_profile(statement_timeout_s=None))
    assert "read_timeout" not in my.connections[0].kwargs


@pytest.mark.parametrize(
    ("extra", "verify_mode", "check_hostname"),
    [
        ({"ssl_verify_identity": False}, ssl.CERT_REQUIRED, False),
        ({"ssl_verify_cert": False}, ssl.CERT_NONE, False),
        ({"ssl_verify_cert": True, "ssl_verify_identity": True}, ssl.CERT_REQUIRED, True),
    ],
)
def test_tls_verification_options(my, extra, verify_mode, check_hostname):
    MySQLWarehouse(my_profile(extra=extra))
    context = my.connections[0].kwargs["ssl"]
    assert context.verify_mode is verify_mode and context.check_hostname is check_hostname


def test_tls_disabled_is_explicit_plaintext_not_preferred_mode(my):
    MySQLWarehouse(my_profile(extra={"ssl_disabled": True}))
    kwargs = my.connections[0].kwargs
    assert kwargs["ssl_disabled"] is True and "ssl" not in kwargs


def test_missing_ca_file_is_a_scrubbed_connect_error(my, tmp_path):
    missing = str(tmp_path / "no-such-ca.pem")
    with pytest.raises(WarehouseError, match="^Could not connect to MySQL") as err:
        MySQLWarehouse(my_profile(extra={"ssl_ca": missing}))
    assert SECRET not in str(err.value)
    assert my.connections == []


@pytest.mark.parametrize(
    "option",
    [
        "local_infile",
        "client_flag",
        "init_command",
        "read_default_file",
        "read_default_group",
        "unix_socket",
        "autocommit",
        "sql_mode",
        "charset",
        "ssl",
        "ssl_cert",
        "ssl_key",
        "cursorclass",
        "conv",
        "bind_address",
        "max_allowed_packet",
        "defer_connect",
        "auth_plugin_map",
        "server_public_key",
    ],
)
def test_options_outside_the_allowlist_are_refused_by_name(my, option):
    with pytest.raises(WarehouseError, match="does not accept") as err:
        MySQLWarehouse(my_profile(extra={option: 1}))
    assert option in str(err.value) and HOST not in str(err.value)
    assert my.connections == []


def test_refusal_says_why_for_the_dangerous_options(my):
    with pytest.raises(WarehouseError, match="LOAD DATA LOCAL") as err:
        MySQLWarehouse(my_profile(extra={"local_infile": True}))
    assert "multi-statements" not in str(err.value)
    with pytest.raises(WarehouseError, match="multi-statements"):
        MySQLWarehouse(my_profile(extra={"client_flag": 65536}))


@pytest.mark.parametrize("option", ["password", "passwd", "ssl_key_password"])
def test_plaintext_secret_options_refused_before_connect(my, option):
    profile = my_profile(extra={option: "plaintextpw"}, secrets_env={})
    with pytest.raises(WarehouseError, match="secrets_env") as err:
        MySQLWarehouse(profile)
    assert "plaintextpw" not in str(err.value)
    assert my.connections == []


def test_only_the_password_secret_is_accepted(my, monkeypatch):
    monkeypatch.setenv("KEY_PW", "key-secret-value")
    profile = my_profile(secrets_env={"password": "MYSQL_PASSWORD", "ssl_key_password": "KEY_PW"})
    with pytest.raises(WarehouseError, match="only 'password'") as err:
        MySQLWarehouse(profile)
    assert "key-secret-value" not in str(err.value)
    assert my.connections == []


@pytest.mark.parametrize("options", [{"host": HOST}, {"host": HOST, "user": ""}])
def test_user_is_required(my, options):
    with pytest.raises(WarehouseError, match="operating-system user"):
        MySQLWarehouse(my_profile(options=options))
    assert my.connections == []


@pytest.mark.parametrize(
    "extra",
    [
        {"ssl_verify_cert": "no"},
        {"ssl_disabled": 1},
        {"port": "3306"},
        {"port": 0},
        {"read_timeout": -1},
        {"connect_timeout": True},
    ],
)
def test_option_values_are_type_checked(my, extra):
    with pytest.raises(WarehouseError, match="must be"):
        MySQLWarehouse(my_profile(extra=extra))
    assert my.connections == []


def test_missing_env_var_names_the_var_only(my, monkeypatch):
    monkeypatch.delenv("MYSQL_PASSWORD")
    with pytest.raises(WarehouseError, match="MYSQL_PASSWORD") as err:
        MySQLWarehouse(my_profile())
    assert SECRET not in str(err.value)


def test_missing_driver_gives_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "pymysql", None)
    with pytest.raises(WarehouseError, match=r"cteforge\[mysql\]"):
        MySQLWarehouse(my_profile())


def test_access_denied_is_scrubbed(my):
    # What MySQL sends for a wrong password: the account names the client host.
    my.connect_error = OperationalError(
        1045, "Access denied for user 'cteforge_ro'@'10.20.30.40' (using password: YES)"
    )
    with pytest.raises(WarehouseError) as err:
        MySQLWarehouse(my_profile())
    message = str(err.value)
    # The account is removed; base.scrub also redacts "password: ..." pairs.
    assert message.startswith("Could not connect to MySQL: Access denied for user *** (")
    for leaked in ("cteforge_ro", "10.20.30.40", "1045"):
        assert leaked not in message
    assert err.value.__cause__ is None and err.value.__suppress_context__


def test_unreachable_host_is_scrubbed(my):
    my.connect_error = OperationalError(
        2003, f"Can't connect to MySQL server on '{HOST}' ([Errno 61] Connection refused)"
    )
    with pytest.raises(WarehouseError) as err:
        MySQLWarehouse(my_profile())
    assert HOST not in str(err.value) and "2003" not in str(err.value)


# ── Session check ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "mode",
    [
        "NO_BACKSLASH_ESCAPES",
        "STRICT_TRANS_TABLES,ANSI_QUOTES",
        "PIPES_AS_CONCAT",
        "REAL_AS_FLOAT,PIPES_AS_CONCAT,ANSI_QUOTES,IGNORE_SPACE,ONLY_FULL_GROUP_BY,ANSI",
    ],
)
def test_sql_modes_that_change_how_sql_reads_are_refused(my, mode):
    my.mode = mode
    with pytest.raises(WarehouseError, match="sql_mode"):
        MySQLWarehouse(my_profile())
    con = my.connections[0]
    assert con.closed is True
    assert con.sent[:2] == [START_READ_ONLY_SQL, "SET SESSION max_execution_time = 60000"]


def test_default_sql_mode_is_accepted_in_a_read_only_transaction(my):
    MySQLWarehouse(my_profile())
    assert my.connections[0].sent == [
        START_READ_ONLY_SQL,
        "SET SESSION max_execution_time = 60000",
        SESSION_MODE_SQL,
        "ROLLBACK",
    ]


# ── Read-only transactions, timeout, single statement ───────────────────────────────


def test_every_call_is_its_own_read_only_transaction_with_timeout(my):
    wh = MySQLWarehouse(my_profile(statement_timeout_s=5))
    con = my.connections[0]
    wh.run("SELECT 1", limit=1)
    wh.explain("SELECT 1")
    assert query_log(con) == [
        START_READ_ONLY_SQL,
        "SET SESSION max_execution_time = 5000",
        "SELECT 1",
        "ROLLBACK",
        START_READ_ONLY_SQL,
        "SET SESSION max_execution_time = 5000",
        "EXPLAIN SELECT 1",
        "ROLLBACK",
    ]
    # The timeout is a bound int parameter, never formatted into the SQL by the connector.
    timeouts = [args for sql, args in con.executed if sql == STATEMENT_TIMEOUT_SQL]
    assert timeouts and all(args == (5000,) and type(args[0]) is int for args in timeouts)


def test_timeout_off_still_read_only(my):
    wh = MySQLWarehouse(my_profile(statement_timeout_s=None))
    wh.run("SELECT 1", limit=1)
    assert query_log(my.connections[0]) == [START_READ_ONLY_SQL, "SELECT 1", "ROLLBACK"]


def test_sql_is_sent_unchanged_without_arguments(my):
    wh = MySQLWarehouse(my_profile())
    con = my.connections[0]
    sql = "SELECT '%s' AS a, '100%%' AS b FROM t WHERE c LIKE 'x%'"
    wh.run(sql, limit=1)
    assert (sql, None) in con.executed and sql in con.sent


def test_multi_statement_string_is_one_execute_and_the_server_error_surfaces(my):
    wh = MySQLWarehouse(my_profile())
    con = my.connections[0]
    sql = "SELECT 1; DROP TABLE t"
    con.fail_on = (
        sql,
        ProgrammingError(1064, "You have an error in your SQL syntax; ... near 'DROP TABLE t'"),
    )
    with pytest.raises(WarehouseError, match="^Query failed: You have an error"):
        wh.run(sql, limit=1)
    assert [s for s in con.sent if "DROP" in s] == [sql]
    assert con.sent[-1] == "ROLLBACK"


@pytest.mark.parametrize(
    "error",
    [
        OperationalError(
            3024, "Query execution was interrupted, maximum statement execution time exceeded"
        ),
        OperationalError(1969, "Query execution was interrupted (max_statement_time exceeded)"),
        OperationalError(2013, "Lost connection to MySQL server during query (timed out)"),
    ],
)
def test_timeouts_are_reported_as_timeouts(my, error):
    wh = MySQLWarehouse(my_profile(statement_timeout_s=2))
    my.connections[0].fail_on = ("SELECT 1", error)
    with pytest.raises(WarehouseError, match=r"^query timed out after 2s$"):
        wh.run("SELECT 1", limit=1)


def test_other_errors_are_not_timeouts_and_keep_only_the_message(my):
    wh = MySQLWarehouse(my_profile(statement_timeout_s=2))
    con = my.connections[0]
    con.fail_on = ("SELECT", ProgrammingError(1146, f"Table 'shop.{SECRET}' doesn't exist"))
    with pytest.raises(WarehouseError) as err:
        wh.run("SELECT * FROM nope", limit=1)
    # The configured database name is a connection detail, removed like the secret.
    assert str(err.value) == "Query failed: Table '***.***' doesn't exist"
    assert err.value.__cause__ is None and err.value.__suppress_context__
    assert con.sent[-1] == "ROLLBACK"


def test_a_cleanup_error_does_not_hide_the_original_error(my):
    wh = MySQLWarehouse(my_profile(statement_timeout_s=2))
    con = my.connections[0]
    con.fail_on = ("SELECT", OperationalError(3024, "maximum statement execution time exceeded"))
    con.close_error = OperationalError(2013, "Lost connection to MySQL server during query")
    with pytest.raises(WarehouseError, match="^query timed out after 2s$"):
        wh.run("SELECT 1", limit=1)


def test_a_cleanup_error_after_success_is_reported(my):
    wh = MySQLWarehouse(my_profile())
    my.connections[0].close_error = OperationalError(2013, "Lost connection to MySQL server")
    with pytest.raises(WarehouseError, match="^Query failed: Lost connection"):
        wh.run("SELECT 1", limit=1)


def test_dropped_connection_is_reopened_and_its_session_checked_again(my):
    wh = MySQLWarehouse(my_profile(statement_timeout_s=None))
    con = my.connections[0]
    con.open = False  # e.g. after a read timeout
    wh.run("SELECT 1", limit=1)
    assert con.reconnects == 1
    assert query_log(con) == [START_READ_ONLY_SQL, SESSION_MODE_SQL, "ROLLBACK"] + [
        START_READ_ONLY_SQL,
        "SELECT 1",
        "ROLLBACK",
    ]


def test_reopened_session_with_a_refused_sql_mode_is_refused(my):
    wh = MySQLWarehouse(my_profile(statement_timeout_s=None))
    con = my.connections[0]
    con.open = False
    con.responses[SESSION_MODE_SQL] = ([("m",)], [(b"ANSI_QUOTES",)])
    with pytest.raises(WarehouseError, match="sql_mode"):
        wh.run("SELECT 1", limit=1)
    assert "SELECT 1" not in con.sent


def test_rollback_is_skipped_when_the_connection_dropped(my):
    wh = MySQLWarehouse(my_profile(statement_timeout_s=None))
    con = my.connections[0]
    error = OperationalError(2013, "Lost connection to MySQL server during query")

    class Dropping(FakeCursor):
        def execute(self, query, args=None):
            if query == "SELECT 1":
                con.open = False
                raise error
            return super().execute(query, args)

    con.cursor = lambda: Dropping(con)
    rollbacks = con.rollbacks
    with pytest.raises(WarehouseError, match="Lost connection"):
        wh.run("SELECT 1", limit=1)
    assert con.rollbacks == rollbacks


# ── Rows, schema, explain, close ────────────────────────────────────────────────────


def test_run_fetches_at_most_limit_plus_one_rows(my):
    wh = MySQLWarehouse(my_profile(max_rows=4, statement_timeout_s=None))
    con = my.connections[0]
    con.responses["SELECT n"] = ([("n",)], [(i,) for i in range(10)])
    small = wh.run("SELECT n", limit=3)
    assert small.rows == [(0,), (1,), (2,)] and small.truncated is True
    assert small.columns == ["n"]
    capped = wh.run("SELECT n", limit=1000)
    assert len(capped.rows) == 4 and capped.truncated is True
    assert con.fetchmany_sizes == [4, 5]
    # Only limit + 1 rows were fetched into memory; the rest was drained by close().
    assert con.drained == (10 - 4) + (10 - 5)
    con.responses["SELECT n"] = ([("n",)], [(1,), (2,)])
    exact = wh.run("SELECT n", limit=2)
    assert exact.rows == [(1,), (2,)] and exact.truncated is False


def test_column_types_use_information_schema_with_bound_parameters(my):
    wh = MySQLWarehouse(my_profile(statement_timeout_s=None))
    con = my.connections[0]
    con.responses["SELECT table_schema"] = (
        [("a",), ("b",), ("c",), ("d",)],
        [
            ("shop", "orders", "ID", "bigint unsigned"),
            ("shop", "orders", "is_test", "tinyint(1)"),
            ("shop", "orders", "status", b"enum('paid','new')"),
        ],
    )
    types = wh.column_types("Shop.Orders_Secret_Name")
    assert types == {
        "id": "bigint unsigned",
        "is_test": "tinyint(1)",
        "status": "enum('paid','new')",
    }
    sql, params = con.executed[-1]
    assert sql == COLUMNS_SQL and params == ("Shop", "Orders_Secret_Name")
    assert "Orders_Secret_Name" not in sql


def test_unqualified_names_resolve_in_the_current_database(my):
    wh = MySQLWarehouse(my_profile(statement_timeout_s=None))
    con = my.connections[0]
    con.responses["SELECT table_schema"] = ([("a",)], [("shop", "orders", "id", "int")])
    assert wh.column_types("orders") == {"id": "int"}
    assert con.executed[-1] == (CURRENT_DATABASE_COLUMNS_SQL, ("orders",))


def test_exact_case_wins_and_ambiguous_names_are_unknown(my):
    wh = MySQLWarehouse(my_profile(statement_timeout_s=None))
    con = my.connections[0]
    con.responses["SELECT table_schema"] = (
        [("a",)],
        [("shop", "Orders", "a", "int"), ("shop", "orders", "b", "text")],
    )
    assert wh.column_types("shop.orders") == {"b": "text"}
    assert wh.column_types("shop.ORDERS") == {}


def test_three_part_and_malformed_names_skip_the_query(my):
    wh = MySQLWarehouse(my_profile())
    before = list(my.connections[0].sent)
    assert wh.column_types("def.shop.orders") == {}
    assert wh.column_types("a..b") == {}
    assert my.connections[0].sent == before


def test_explain_failure_is_scrubbed(my):
    wh = MySQLWarehouse(my_profile())
    con = my.connections[0]
    con.fail_on = ("EXPLAIN", ProgrammingError(1054, "Unknown column 'nope' in 'field list'"))
    with pytest.raises(WarehouseError, match="^Query does not compile: Unknown column"):
        wh.explain("SELECT nope FROM t")


def test_close_is_idempotent_and_later_calls_fail(my):
    wh = MySQLWarehouse(my_profile())
    wh.close()
    wh.close()
    assert my.connections[0].closed is True
    with pytest.raises(WarehouseError, match="closed"):
        wh.run("SELECT 1", limit=1)
    assert wh.dialect is Dialect.MYSQL


def test_error_message_keeps_the_server_text_only():
    exc = OperationalError(1045, "Access denied for user 'u'@'host.example' (using password: NO)")
    assert error_message(exc) == "Access denied for user *** (using password: NO)"
    assert error_message(ValueError("plain")) == "plain"


def test_connections_yml_mysql_profile_opens_this_connector(my):
    from cteforge.files.connections import profiles_from_yaml
    from cteforge.services.connections import open_profile

    (profile,) = profiles_from_yaml(
        "profiles:\n  wh:\n    dialect: mysql\n"
        f"    options: {{host: {HOST}, port: 3306, database: shop, user: ro}}\n"
        "    secrets_env: {password: MYSQL_PASSWORD}\n"
    )
    assert profile.dialect is Dialect.MYSQL
    wh = open_profile(profile)
    assert isinstance(wh, MySQLWarehouse)
    assert my.connections[0].kwargs["password"] == SECRET
