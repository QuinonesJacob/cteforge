"""ClickHouse connector via clickhouse-connect, ClickHouse Inc.'s HTTP(S) driver, Apache-2.0
(HARDENED module — read CLAUDE.md here before editing). Install with
``pip install 'cteforge[clickhouse]'``. ADR 0025.

Options: only those in ``ALLOWED_OPTIONS`` (host, port, database, user, secure, verify,
ca_cert, connect_timeout, send_receive_timeout, and the scan guards max_rows_to_read /
max_bytes_to_read). Others are refused by name, among them ``dsn`` (may embed a
password), ``interface`` (``chdb`` would run an embedded engine), ``access_token`` /
``client_cert`` / ``client_cert_key`` (credentials from elsewhere than ``secrets_env``) and
``settings`` (the connector owns the query settings). The password is referenced by env
var: ``secrets_env: {password: CH_PASSWORD}``. ``user`` is required: clickhouse-connect
would otherwise connect as ``default``, usually an administrator.

Every request carries these ClickHouse settings (HTTP parameters, never SQL text):
- ``readonly = 2``: only read queries; settings may change, except ``readonly`` itself
  (ClickHouse Settings.cpp: "2 - only read requests, as well as changing settings, except
  for the 'readonly' setting"). Not ``readonly = 1``, which would also refuse the
  ``max_execution_time`` sent with the same request and a definition's own ``SETTINGS``
  clause. Under readonly INSERT, DDL, OPTIMIZE, SYSTEM, KILL and grants are refused
  (ContextAccess.cpp). A query may still raise its own limits in a ``SETTINGS`` clause;
  only the user's settings profile (constraints) can stop that — see the README.
- ``max_execution_time = statement_timeout_s`` with ``timeout_overflow_mode = 'throw'``:
  ClickHouse fails the query with TIMEOUT_EXCEEDED (code 159), raised here as
  ``query timed out after Ns``. The HTTP read timeout (``send_receive_timeout``) defaults
  to the statement timeout + 30 s, and ``cancel_http_readonly_queries_on_client_close = 1``
  makes the server cancel a read query whose client has gone.
- Scan guards, only when the profile sets them: ``max_rows_to_read`` /
  ``max_bytes_to_read`` with ``read_overflow_mode = 'throw'`` (checked per processed
  chunk on the deepest table expression; on each remote server separately).

Single statement: the HTTP interface parses each request with multi-statements disabled
(executeQuery → parseQuery(..., allow_multi_statements = false): "Multi-statements are not
allowed"); clickhouse-connect sends the SQL as given (only a trailing ``;`` is stripped)
with no client-side parameter binding when no parameters are passed.

Rows: ``run`` streams result blocks and stops reading after ``limit + 1`` rows; closing the
stream closes the HTTP response (the server cancels the query, see above). Not
``max_result_rows`` + ``result_overflow_mode = 'break'``: ClickHouse applies that to
subqueries too, which would silently truncate them.

Column types come from ``system.columns`` with server-side bound parameters
(``{database:String}``); names compare case-insensitively like the other connectors.
``explain`` runs ``EXPLAIN PLAN`` (builds the plan without running the query).

Errors: clickhouse-connect is set to ``show_clickhouse_errors = 'scrub'`` (no URL or
server version), then only the last ``DB::Exception:`` message is kept (no ``Code:`` /
``Received from <host>`` prefixes, version trailer or stack trace) and scrubbed as for
every connector. No sessions (``autogenerate_session_id = False``): nothing persists
between calls. The driver's logger is set to ERROR unless the operator set a level: its
warnings carry transport errors that may name the server.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any, TypeVar

from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import WarehouseError
from cteforge.warehouse.base import (
    QueryResult,
    Sensitive,
    connect_kwargs,
    effective_limit,
    is_timeout,
    load_driver,
    reject_plaintext_secrets,
    resolve_secrets,
    sensitive_values,
    split_table,
    timeout_error,
)

T = TypeVar("T")

SCAN_GUARDS = ("max_rows_to_read", "max_bytes_to_read")
ALLOWED_OPTIONS = frozenset(
    {
        "host",
        "port",
        "database",
        "user",
        "secure",
        "verify",
        "ca_cert",
        "connect_timeout",
        "send_receive_timeout",
        *SCAN_GUARDS,
    }
)
ALLOWED_SECRETS = frozenset({"password"})
READONLY = 2
TIMEOUT_EXCEEDED = 159  # ClickHouse error code (ErrorCodes.cpp) for max_execution_time.
TIMEOUT_MARGIN_S = 30  # HTTP read timeout beyond the statement timeout.
EXPLAIN_PREFIX = "EXPLAIN PLAN "
# Identifiers are bound server-side ({name:Type} → HTTP param_name), never formatted in.
COLUMNS_SQL = (
    "SELECT database, table, name, type FROM system.columns "
    "WHERE lower(database) = lower({database:String}) AND lower(table) = lower({table:String}) "
    "ORDER BY database, table, position"
)
CURRENT_DATABASE_COLUMNS_SQL = (
    "SELECT database, table, name, type FROM system.columns "
    "WHERE database = currentDatabase() AND lower(table) = lower({table:String}) "
    "ORDER BY database, table, position"
)
_DRIVER_LOGGER = "clickhouse_connect"
_EXCEPTION_MARKER = "DB::Exception:"
_VERSION_TRAILER = re.compile(r"\s*\(version\s+(?:[^()]|\([^()]*\))*\)\s*\.?\s*$")
_STACK_TRACE = re.compile(r"\bStack trace\b.*", re.DOTALL | re.IGNORECASE)
_WHAT = re.compile(r",?\s*e\.what\(\)\s*=\s*\S+")
_FOR_URL = re.compile(r"\s*\(for url [^)]*\)")
_RECEIVED_FROM = re.compile(r"\bReceived from \S+", re.IGNORECASE)
_ERROR_NAME = re.compile(r"\(([A-Z][A-Z0-9_]+)\)")  # e.g. (UNKNOWN_TABLE)


def _check_options(profile: WarehouseProfile) -> None:
    unknown = sorted(set(profile.options) - ALLOWED_OPTIONS)
    if unknown:
        raise WarehouseError(
            f"Profile '{profile.name}' sets options {unknown} the ClickHouse connector does not "
            f"accept; allowed: {sorted(ALLOWED_OPTIONS)}"
        )
    secrets = sorted(set(profile.secrets_env) - ALLOWED_SECRETS)
    if secrets:
        raise WarehouseError(
            f"Profile '{profile.name}' sets secrets_env {secrets}; the ClickHouse connector "
            "accepts only 'password'"
        )
    user = profile.options.get("user")
    if not isinstance(user, str) or not user:
        raise WarehouseError(
            f"Profile '{profile.name}' must set options.user to a dedicated read-only ClickHouse "
            "user (without it the driver connects as 'default')"
        )


def _positive_int(profile: WarehouseProfile, name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise WarehouseError(f"Profile '{profile.name}': {name} must be a positive integer")
    return value


def query_settings(profile: WarehouseProfile) -> dict[str, Any]:
    """The ClickHouse settings sent with every request (see the module docstring)."""
    settings: dict[str, Any] = {
        "readonly": READONLY,
        "cancel_http_readonly_queries_on_client_close": 1,
    }
    if profile.statement_timeout_s is not None:
        settings["max_execution_time"] = profile.statement_timeout_s
        settings["timeout_overflow_mode"] = "throw"
    guards = {
        name: _positive_int(profile, name, profile.options[name])
        for name in SCAN_GUARDS
        if name in profile.options
    }
    if guards:
        settings.update(guards)
        settings["read_overflow_mode"] = "throw"
    return settings


def _client_kwargs(profile: WarehouseProfile, secrets: dict[str, str]) -> dict[str, Any]:
    kwargs = {k: v for k, v in connect_kwargs(profile, secrets).items() if k not in SCAN_GUARDS}
    kwargs["username"] = kwargs.pop("user")
    kwargs["interface"] = "https" if kwargs.get("secure") else "http"
    if profile.statement_timeout_s is not None:
        kwargs.setdefault("send_receive_timeout", profile.statement_timeout_s + TIMEOUT_MARGIN_S)
    kwargs.update(
        query_limit=0,  # the connector applies the row limit itself
        autogenerate_session_id=False,
        show_clickhouse_errors="scrub",
        client_name="cteforge",
    )
    return kwargs


def error_message(exc: BaseException) -> str:
    """The useful part of a ClickHouse error: the last ``DB::Exception:`` message without
    the ``Code: N.`` / ``Received from <host>`` prefixes, version trailer, ``(for url ...)``
    or stack trace. Not yet scrubbed of connection details (Sensitive.scrub does that)."""
    text = _FOR_URL.sub("", str(exc))
    names = _ERROR_NAME.findall(text)
    text = _STACK_TRACE.sub("", text)  # frames name DB::Exception too
    if _EXCEPTION_MARKER in text:
        text = text.rsplit(_EXCEPTION_MARKER, 1)[1]
    text = _WHAT.sub("", text)
    text = _RECEIVED_FROM.sub("Received from ***", text)
    text = _VERSION_TRAILER.sub("", " ".join(text.split())).strip()
    if names and f"({names[-1]})" not in text:  # the symbolic name followed the stack trace
        text = f"{text} ({names[-1]})"
    return text


def _is_timeout(exc: BaseException) -> bool:
    """TIMEOUT_EXCEEDED from the server, or the HTTP read timing out (urllib3's
    ReadTimeoutError, which clickhouse-connect wraps in an OperationalError)."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if getattr(current, "code", None) == TIMEOUT_EXCEEDED:
            return True
        if getattr(current, "name", None) == "TIMEOUT_EXCEEDED":
            return True
        if is_timeout(current) or type(current).__name__ == "ReadTimeoutError":
            return True
        current = current.__cause__ or current.__context__
    return False


def _quiet_driver_logs() -> None:
    logger = logging.getLogger(_DRIVER_LOGGER)
    if logger.level == logging.NOTSET:
        logger.setLevel(logging.ERROR)


class ClickHouseWarehouse:
    dialect: Dialect = Dialect.CLICKHOUSE

    def __init__(self, profile: WarehouseProfile) -> None:
        driver = load_driver("clickhouse_connect", "clickhouse")
        common = load_driver("clickhouse_connect.common", "clickhouse")
        reject_plaintext_secrets(profile)
        _check_options(profile)
        if common.get_setting("invalid_setting_action") == "drop":
            # The driver would then silently drop settings it believes read-only for the
            # user (incl. readonly / max_execution_time) instead of failing.
            raise WarehouseError(
                "clickhouse-connect's common setting invalid_setting_action is 'drop'; the "
                "ClickHouse connector needs 'error' (the default) so its settings are never "
                "silently dropped"
            )
        secrets = resolve_secrets(profile)
        self._sensitive: Sensitive = sensitive_values(profile, secrets)
        self._settings = query_settings(profile)
        self._max_rows = profile.max_rows
        self._timeout_s = profile.statement_timeout_s
        _quiet_driver_logs()
        try:
            self._client = driver.create_client(**_client_kwargs(profile, secrets))
        except Exception as exc:
            raise self._error("Could not connect to ClickHouse", exc) from None

    def _error(self, context: str, exc: BaseException) -> WarehouseError:
        detail = self._sensitive.scrub(error_message(exc)) or type(exc).__name__
        cause = exc.__cause__
        if cause is not None and getattr(exc, "code", None) is None:
            # Transport failures ("Error executing HTTP request"): name the cause's type
            # only; its message names the server.
            detail = f"{detail} ({type(cause).__name__})"
        return WarehouseError(f"{context}: {detail}")

    def _call(self, context: str, action: Callable[[], T]) -> T:
        try:
            return action()
        except WarehouseError as exc:
            raise WarehouseError(self._sensitive.scrub(str(exc))) from None
        except Exception as exc:
            if self._timeout_s is not None and _is_timeout(exc):
                raise timeout_error(self._timeout_s) from None
            raise self._error(context, exc) from None

    def close(self) -> None:
        try:
            self._client.close()
        except Exception as exc:
            raise self._error("Could not close connection", exc) from None

    def column_types(self, table: str) -> dict[str, str]:
        parts = split_table(table)
        if parts is None or parts[0] is not None:  # ClickHouse names are [database.]table
            return {}
        _, database, name = parts
        if database is None:
            sql, params = CURRENT_DATABASE_COLUMNS_SQL, {"table": name}
        else:
            sql, params = COLUMNS_SQL, {"database": database, "table": name}

        def action() -> list[Any]:
            result = self._client.query(sql, parameters=params, settings=self._settings)
            return list(result.result_rows)

        rows = self._call("Schema lookup failed", action)
        return _columns_of_one_table(rows, database, name)

    def explain(self, sql: str) -> None:
        def action() -> None:
            self._client.query(EXPLAIN_PREFIX + sql, settings=self._settings)

        self._call("Query does not compile", action)

    def run(self, sql: str, limit: int) -> QueryResult:
        limit = effective_limit(limit, self._max_rows)

        def action() -> QueryResult:
            rows: list[Any] = []
            with self._client.query_row_block_stream(sql, settings=self._settings) as stream:
                columns = [str(c) for c in stream.source.column_names]
                for block in stream:
                    rows.extend(block)
                    if len(rows) > limit:
                        break  # leaving the block closes the response; the server cancels
            return QueryResult(
                columns=columns,
                rows=[tuple(r) for r in rows[:limit]],
                truncated=len(rows) > limit,
            )

        return self._call("Query failed", action)


def _columns_of_one_table(rows: list[Any], database: str | None, name: str) -> dict[str, str]:
    """Rows (database, table, column, type) → column (lower-case) → type for ONE table:
    the exact-case match when there is one, else the only case-insensitive match; empty
    when the name is ambiguous or unknown. A column name repeated in another case keeps
    the first (ClickHouse names are case-sensitive; the Warehouse protocol is not)."""
    exact = [r for r in rows if r[1] == name and (database is None or r[0] == database)]
    chosen = exact or rows
    if len({(r[0], r[1]) for r in chosen}) != 1:
        return {}
    columns: dict[str, str] = {}
    for _db, _table, column, dtype in chosen:
        columns.setdefault(str(column).lower(), str(dtype))
    return columns
