"""ClickHouse connector (ADR 0025) against a fake `clickhouse_connect` module.

The fake mirrors what clickhouse-connect 1.13 does that matters here: `create_client(**kw)`
(connects at once), `client.query(sql, parameters=, settings=)` returning `result_rows`,
`client.query_row_block_stream(sql, settings=)` returning a context manager whose
`source.column_names` names the columns and whose iteration yields row blocks (leaving it
closes the response), the common setting `invalid_setting_action`, and server errors raised
as `DatabaseError(message, code=, name=)` with the server's text in the message. What only
a real server can show (readonly refusals, multi-statement refusal, timeouts, scan guards)
is in tests/integration/test_clickhouse_live.py.
"""

import logging
import sys
from types import ModuleType

import pytest

from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import WarehouseError
from cteforge.warehouse.clickhouse import (
    COLUMNS_SQL,
    CURRENT_DATABASE_COLUMNS_SQL,
    ClickHouseWarehouse,
    error_message,
)

SECRET = "ch-hunter2-very-secret"
HOST = "ch-prod-1.internal.example.com"
USER = "cteforge_ro"


class DatabaseError(Exception):
    def __init__(self, *args, code=None, name=None):
        super().__init__(*args)
        self.code = code
        self.name = name


class OperationalError(DatabaseError):
    pass


class ReadTimeoutError(Exception):
    pass


def server_error(code: int, name: str, message: str) -> DatabaseError:
    """What the driver raises for a server error with show_clickhouse_errors='scrub'."""
    return DatabaseError(
        f"Received ClickHouse exception, code: {code}, server response: Code: {code}. "
        f"DB::Exception: {message} ({name})",
        code=code,
        name=name,
    )


class FakeStream:
    def __init__(self, client: "FakeClient", columns: tuple, blocks: list) -> None:
        self.client = client
        self.source = type("Result", (), {"column_names": columns})()
        self._blocks = iter(blocks)
        self.entered = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *exc):
        self.client.streams_closed += 1
        return False

    def __iter__(self):
        return self

    def __next__(self):
        if not self.entered:
            raise RuntimeError("Stream should be used within a context")
        block = next(self._blocks)
        self.client.blocks_read += 1
        return block


class FakeClient:
    def __init__(self, kwargs: dict) -> None:
        self.kwargs = kwargs
        self.calls: list[tuple[str, dict | None, dict | None]] = []
        self.rows: list[tuple] = []
        self.columns: tuple = ("x",)
        self.blocks: list[list[tuple]] = []
        self.fail: Exception | None = None
        self.blocks_read = 0
        self.streams_closed = 0
        self.closed = False

    def query(self, query=None, parameters=None, settings=None):
        self.calls.append((query, parameters, settings))
        if self.fail is not None:
            raise self.fail
        return type("Result", (), {"result_rows": list(self.rows)})()

    def query_row_block_stream(self, query=None, parameters=None, settings=None):
        self.calls.append((query, parameters, settings))
        if self.fail is not None:
            raise self.fail
        return FakeStream(self, self.columns, self.blocks)

    def close(self):
        self.closed = True


@pytest.fixture()
def ch(monkeypatch):
    module = ModuleType("clickhouse_connect")
    common = ModuleType("clickhouse_connect.common")
    module.clients = []
    module.connect_error = None
    settings = {"invalid_setting_action": "error"}
    common.get_setting = settings.__getitem__
    common.values = settings

    def create_client(**kwargs):
        if module.connect_error is not None:
            raise module.connect_error
        client = FakeClient(kwargs)
        module.clients.append(client)
        return client

    module.create_client = create_client
    module.common = common
    monkeypatch.setitem(sys.modules, "clickhouse_connect", module)
    monkeypatch.setitem(sys.modules, "clickhouse_connect.common", common)
    monkeypatch.setenv("CH_PASSWORD", SECRET)
    return module


def ch_profile(**kwargs) -> WarehouseProfile:
    options = {"host": HOST, "port": 8443, "secure": True, "database": "agent", "user": USER}
    base = {
        "name": "ch",
        "dialect": Dialect.CLICKHOUSE,
        "options": options | kwargs.pop("options", {}),
        "secrets_env": {"password": "CH_PASSWORD"},
    }
    return WarehouseProfile(**(base | kwargs))


def opened(ch, **kwargs) -> tuple[ClickHouseWarehouse, FakeClient]:
    warehouse = ClickHouseWarehouse(ch_profile(**kwargs))
    return warehouse, ch.clients[-1]


# ── Connecting ──────────────────────────────────────────────────────────────────────


def test_connects_with_the_password_from_the_environment_and_safe_driver_options(ch):
    _, client = opened(ch)
    assert client.kwargs == {
        "host": HOST,
        "port": 8443,
        "secure": True,
        "database": "agent",
        "username": USER,
        "password": SECRET,
        "interface": "https",
        "send_receive_timeout": 90,  # statement timeout (60 s) + 30 s
        "query_limit": 0,
        "autogenerate_session_id": False,
        "show_clickhouse_errors": "scrub",
        "client_name": "cteforge",
    }


def test_plain_http_and_an_explicit_read_timeout_are_kept(ch):
    _, client = opened(ch, options={"secure": False, "port": 8123, "send_receive_timeout": 10})
    assert client.kwargs["interface"] == "http"
    assert client.kwargs["send_receive_timeout"] == 10


def test_no_statement_timeout_leaves_the_driver_read_timeout_alone(ch):
    _, client = opened(ch, statement_timeout_s=None)
    assert "send_receive_timeout" not in client.kwargs


@pytest.mark.parametrize(
    "option",
    ["dsn", "interface", "access_token", "client_cert", "client_cert_key", "settings", "proxy"],
)
def test_options_outside_the_allowlist_are_refused(ch, option):
    # dsn / access_token are also secret-shaped names, refused before the allowlist.
    with pytest.raises(WarehouseError, match="does not accept|secrets_env"):
        ClickHouseWarehouse(ch_profile(options={option: "x"}))
    assert ch.clients == []


def test_plaintext_password_is_refused(ch):
    with pytest.raises(WarehouseError, match="secrets_env"):
        ClickHouseWarehouse(ch_profile(options={"password": "x"}))


def test_only_the_password_secret_is_accepted(ch, monkeypatch):
    monkeypatch.setenv("CH_TOKEN", "t")
    with pytest.raises(WarehouseError, match="accepts only 'password'"):
        ClickHouseWarehouse(ch_profile(secrets_env={"access_token": "CH_TOKEN"}))


def test_a_user_is_required(ch):
    profile = ch_profile()
    options = {k: v for k, v in profile.options.items() if k != "user"}
    with pytest.raises(WarehouseError, match="options.user"):
        ClickHouseWarehouse(profile.model_copy(update={"options": options}))


def test_missing_password_variable_names_the_variable_not_a_value(ch, monkeypatch):
    monkeypatch.delenv("CH_PASSWORD")
    with pytest.raises(WarehouseError, match="CH_PASSWORD"):
        ClickHouseWarehouse(ch_profile())


def test_refuses_a_driver_that_would_drop_settings(ch):
    ch.common.values["invalid_setting_action"] = "drop"
    with pytest.raises(WarehouseError, match="invalid_setting_action"):
        ClickHouseWarehouse(ch_profile())
    assert ch.clients == []


def test_connect_error_is_scrubbed_and_names_only_the_cause_type(ch):
    class NewConnectionError(Exception):
        pass

    error = OperationalError("Error executing HTTP request")
    error.__cause__ = NewConnectionError(f"Failed to connect to {HOST}:8443 as {USER}")
    ch.connect_error = error
    with pytest.raises(WarehouseError) as info:
        ClickHouseWarehouse(ch_profile())
    message = str(info.value)
    assert message == (
        "Could not connect to ClickHouse: Error executing HTTP request (NewConnectionError)"
    )


def test_authentication_error_hides_user_password_host_and_version(ch):
    ch.connect_error = server_error(
        516,
        "AUTHENTICATION_FAILED",
        f"{USER}: Authentication failed: password is incorrect, or there is no user with "
        f"such name. Tried {SECRET} at {HOST} 10.1.2.3:8443. (version 24.8.4.13 (official build))",
    )
    with pytest.raises(WarehouseError) as info:
        ClickHouseWarehouse(ch_profile())
    message = str(info.value)
    for leaked in (USER, SECRET, HOST, "10.1.2.3", "24.8.4.13", "Code:", "Received ClickHouse"):
        assert leaked not in message
    assert "Authentication failed" in message


def test_driver_logs_are_quieted_unless_configured(ch):
    logger = logging.getLogger("clickhouse_connect")
    logger.setLevel(logging.NOTSET)
    opened(ch)
    assert logger.level == logging.ERROR
    logger.setLevel(logging.DEBUG)
    opened(ch)
    assert logger.level == logging.DEBUG
    logger.setLevel(logging.NOTSET)


# ── Settings on every request ───────────────────────────────────────────────────────


def test_every_request_is_readonly_with_the_statement_timeout(ch):
    warehouse, client = opened(ch)
    client.rows = [("agent", "traces", "model", "String")]
    warehouse.column_types("traces")
    warehouse.explain("SELECT 1")
    warehouse.run("SELECT 1", 10)
    expected = {
        "readonly": 2,
        "cancel_http_readonly_queries_on_client_close": 1,
        "max_execution_time": 60,
        "timeout_overflow_mode": "throw",
    }
    assert [settings for _, _, settings in client.calls] == [expected] * 3


def test_scan_guards_are_sent_only_when_the_profile_sets_them(ch):
    warehouse, client = opened(
        ch,
        options={"max_rows_to_read": 1_000_000, "max_bytes_to_read": 10**10},
        statement_timeout_s=None,
    )
    warehouse.run("SELECT 1", 1)
    _, _, settings = client.calls[-1]
    assert settings == {
        "readonly": 2,
        "cancel_http_readonly_queries_on_client_close": 1,
        "max_rows_to_read": 1_000_000,
        "max_bytes_to_read": 10**10,
        "read_overflow_mode": "throw",
    }
    assert "max_rows_to_read" not in client.kwargs


@pytest.mark.parametrize("value", [0, -5, True, "1000"])
def test_scan_guards_must_be_positive_integers(ch, value):
    with pytest.raises(WarehouseError, match="max_rows_to_read must be a positive integer"):
        ClickHouseWarehouse(ch_profile(options={"max_rows_to_read": value}))


# ── run ─────────────────────────────────────────────────────────────────────────────


def test_run_sends_the_sql_unchanged_and_without_parameters(ch):
    warehouse, client = opened(ch)
    sql = "SELECT model FROM agent.traces WHERE note = '{not_a_param:String}'"
    warehouse.run(sql, 5)
    query, parameters, _ = client.calls[-1]
    assert query == sql and parameters is None


def test_run_stops_reading_after_limit_plus_one_rows(ch):
    warehouse, client = opened(ch)
    client.columns = ("model", "n")
    client.blocks = [[("a", 1), ("b", 2)], [("c", 3), ("d", 4)], [("e", 5)]]
    result = warehouse.run("SELECT model, n FROM t", 2)
    assert result.columns == ["model", "n"]
    assert result.rows == [("a", 1), ("b", 2)]
    assert result.truncated is True
    assert client.blocks_read == 2  # the third block is never read
    assert client.streams_closed == 1


def test_run_returns_everything_under_the_limit(ch):
    warehouse, client = opened(ch)
    client.blocks = [[(1,), (2,)]]
    result = warehouse.run("SELECT 1", 10)
    assert result.rows == [(1,), (2,)] and result.truncated is False


def test_run_limit_never_exceeds_the_profile_cap(ch):
    warehouse, client = opened(ch, max_rows=3)
    client.blocks = [[(i,) for i in range(10)]]
    result = warehouse.run("SELECT 1", 1000)
    assert len(result.rows) == 3 and result.truncated is True


def test_run_error_keeps_only_the_message(ch):
    warehouse, client = opened(ch)
    client.fail = server_error(
        62,
        "SYNTAX_ERROR",
        "Syntax error (Multi-statements are not allowed): failed at position 9 (end of query)"
        " (line 1, col 9): ; DROP TABLE t FORMAT Native. . (version 25.3.1.1 (official build))"
        "\nStack trace (when copying this message, always include the lines below):\n"
        "0. DB::Exception::Exception() @ 0x000000000c8a2bdb in /usr/bin/clickhouse",
    )
    with pytest.raises(WarehouseError) as info:
        warehouse.run("SELECT 1; DROP TABLE t", 10)
    message = str(info.value)
    assert message.startswith("Query failed: Syntax error (Multi-statements are not allowed)")
    for leaked in ("Stack trace", "/usr/bin/clickhouse", "25.3.1.1", "Code: 62"):
        assert leaked not in message


# ── Timeouts ────────────────────────────────────────────────────────────────────────


def test_timeout_exceeded_reads_as_a_timeout(ch):
    warehouse, client = opened(ch)
    client.fail = server_error(
        159, "TIMEOUT_EXCEEDED", "Timeout exceeded: elapsed 60.0 seconds, maximum: 60"
    )
    with pytest.raises(WarehouseError, match=r"^query timed out after 60s$"):
        warehouse.run("SELECT sleepEachRow(1) FROM numbers(100)", 10)


def test_http_read_timeout_reads_as_a_timeout(ch):
    warehouse, client = opened(ch, statement_timeout_s=5)
    error = OperationalError("Error executing HTTP request")
    error.__cause__ = ReadTimeoutError(f"HTTPSConnectionPool(host='{HOST}'): Read timed out.")
    client.fail = error
    with pytest.raises(WarehouseError, match=r"^query timed out after 5s$"):
        warehouse.run("SELECT 1", 10)


def test_without_a_timeout_a_timeout_error_is_just_scrubbed(ch):
    warehouse, client = opened(ch, statement_timeout_s=None)
    client.fail = server_error(159, "TIMEOUT_EXCEEDED", "Timeout exceeded")
    with pytest.raises(WarehouseError, match="Query failed: Timeout exceeded"):
        warehouse.run("SELECT 1", 10)


def test_scan_guard_errors_are_reported(ch):
    warehouse, client = opened(ch, options={"max_rows_to_read": 1000})
    client.fail = server_error(
        158, "TOO_MANY_ROWS", "Limit for rows (controlled by 'max_rows_to_read' setting) exceeded"
    )
    with pytest.raises(WarehouseError, match="max_rows_to_read"):
        warehouse.run("SELECT * FROM agent.traces", 10)


# ── column_types / explain ──────────────────────────────────────────────────────────


def test_column_types_bind_database_and_table_as_parameters(ch):
    warehouse, client = opened(ch)
    client.rows = [
        ("agent", "traces", "trace_id", "UUID"),
        ("agent", "traces", "Model", "LowCardinality(String)"),
    ]
    assert warehouse.column_types("agent.traces") == {
        "trace_id": "UUID",
        "model": "LowCardinality(String)",
    }
    assert client.calls[-1][:2] == (COLUMNS_SQL, {"database": "agent", "table": "traces"})


def test_unqualified_table_resolves_in_the_current_database(ch):
    warehouse, client = opened(ch)
    client.rows = [("agent", "traces", "model", "String")]
    assert warehouse.column_types("traces") == {"model": "String"}
    assert client.calls[-1][:2] == (CURRENT_DATABASE_COLUMNS_SQL, {"table": "traces"})


def test_hostile_table_names_stay_parameters(ch):
    warehouse, client = opened(ch)
    warehouse.column_types("agent.t'; DROP TABLE x; --")
    query, parameters, _ = client.calls[-1]
    assert query == COLUMNS_SQL
    assert parameters == {"database": "agent", "table": "t'; DROP TABLE x; --"}


def test_three_part_names_are_not_clickhouse_tables(ch):
    warehouse, client = opened(ch)
    assert warehouse.column_types("a.b.c") == {}
    assert client.calls == []


def test_exact_case_wins_and_ambiguous_names_give_nothing(ch):
    warehouse, client = opened(ch)
    client.rows = [
        ("agent", "Traces", "a", "String"),
        ("agent", "traces", "b", "UInt64"),
    ]
    assert warehouse.column_types("agent.traces") == {"b": "UInt64"}
    assert warehouse.column_types("agent.TRACES") == {}  # two tables match, none exactly


def test_explain_runs_explain_plan(ch):
    warehouse, client = opened(ch)
    warehouse.explain("SELECT 1")
    assert client.calls[-1][0] == "EXPLAIN PLAN SELECT 1"


def test_explain_error_is_scrubbed(ch):
    warehouse, client = opened(ch)
    client.fail = server_error(
        60,
        "UNKNOWN_TABLE",
        f"Received from {HOST}:9000. DB::Exception: Table logs.x does not exist",
    )
    with pytest.raises(WarehouseError) as info:
        warehouse.explain("SELECT * FROM logs.x")
    assert str(info.value) == (
        "Query does not compile: Table logs.x does not exist (UNKNOWN_TABLE)"
    )


def test_close_closes_the_client(ch):
    warehouse, client = opened(ch)
    warehouse.close()
    assert client.closed


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "Received ClickHouse exception, code: 47, server response: Code: 47. "
            "DB::Exception: Unknown expression identifier `x` in scope SELECT x. "
            "(UNKNOWN_IDENTIFIER) (version 24.8.4.13 (official build)) (for url https://h:8443)",
            "Unknown expression identifier `x` in scope SELECT x. (UNKNOWN_IDENTIFIER)",
        ),
        (
            "Code: 164. DB::Exception: readonly: Cannot execute query in readonly mode. (READONLY)",
            "readonly: Cannot execute query in readonly mode. (READONLY)",
        ),
        ("plain driver message", "plain driver message"),
    ],
)
def test_error_message_keeps_the_useful_part(raw, expected):
    assert error_message(Exception(raw)) == expected
