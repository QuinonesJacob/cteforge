"""MySQL connector via PyMySQL, a pure-Python driver, MIT (HARDENED module — read CLAUDE.md
here before editing). Install with ``pip install 'cteforge[mysql]'``. ADR 0028. Reviewed
against PyMySQL 1.2.4; tested against MySQL 8 only. MariaDB is untested (best effort, not
supported): it has no ``max_execution_time`` (its own is ``max_statement_time``), so the
statement timeout below would fail there.

Options: only those in ``ALLOWED_OPTIONS`` (host, port, database, user, ssl_ca,
ssl_verify_cert, ssl_verify_identity, ssl_disabled, connect_timeout, read_timeout). Every
other PyMySQL keyword is refused by name (``REFUSED_OPTIONS`` says why for the dangerous
ones): ``local_infile`` (LOAD DATA LOCAL lets the server read client files), ``client_flag``
(could enable CLIENT.MULTI_STATEMENTS), ``init_command`` / ``sql_mode`` / ``autocommit``
(the connector owns the session), ``read_default_file`` / ``read_default_group`` (user,
password, host and TLS files read from a my.cnf outside ``secrets_env``), ``unix_socket``,
``charset`` (fixed to utf8mb4), client certificates (``ssl_cert`` / ``ssl_key`` /
``ssl_key_password``: credentials from elsewhere) and ``ssl`` (the connector builds the TLS
context). The password is referenced by env var: ``secrets_env: {password: MYSQL_PASSWORD}``.
``user`` is required: PyMySQL would otherwise log in as the OS user (getpass.getuser()).

TLS: verified by default. The connector passes its own ``ssl.SSLContext`` (system trust
store, or ``ssl_ca``; certificate and host name checked; TLS 1.2+), which PyMySQL uses as
given and then requires TLS (connections.py: ``if ssl: ... self._ssl_required = True``;
the handshake fails with "SSL is required but the server doesn't support it"). Without
it PyMySQL would use PREFERRED mode: TLS without certificate checks, silently plaintext
if the server has no TLS. ``ssl_verify_identity: false`` (CA-checked, any host name) and
``ssl_verify_cert: false`` (encrypted, unverified) exist for test servers with
auto-generated certificates; ``ssl_disabled: true`` sends plaintext (local servers only).

Charset: always ``utf8mb4`` (PyMySQL sends it in the handshake and as SET NAMES). With a
UTF-8 connection no multi-byte character contains the bytes of ``'`` or ``\\``, so the
escaping in rendered literals cannot be split the way GBK / SJIS / BIG5 allow.

Session: at connect (and after a reconnect) ``SELECT @@SESSION.sql_mode`` must not contain
NO_BACKSLASH_ESCAPES, ANSI_QUOTES or PIPES_AS_CONCAT (``ANSI`` includes the last two):
cteforge reads and writes MySQL SQL as sqlglot does under the default sql_mode, where ``\\``
escapes in strings, ``"..."`` is a string and ``||`` is OR. Under those modes rendered
literals would change value (never break out of the literal: quotes are doubled) and
trusted SQL would mean something else, so the connector refuses to start.

Single statement: PyMySQL's capability flags (constants/CLIENT.py ``CAPABILITIES``) do not
include CLIENT.MULTI_STATEMENTS and ``client_flag`` is refused, so the server rejects a
string holding several statements with a syntax error (1064). ``cursor.execute(sql)``
without arguments sends the SQL unchanged (cursors.py: ``if args is not None: query =
self._mogrify(...)``), so ``%`` in SQL is never interpreted.

Read-only: every call is ``START TRANSACTION READ ONLY`` → statement(s) → ROLLBACK
(autocommit off, PyMySQL's default, set explicitly). MySQL refuses writes to non-temporary
tables in a READ ONLY transaction (ER_CANT_EXECUTE_IN_READ_ONLY_TRANSACTION, 1792). That
is defence in depth, not the guarantee: READ ONLY does not stop ``SELECT ... INTO OUTFILE``
/ ``DUMPFILE`` (files on the server), ``LOAD_FILE()`` or locking functions. Those are refused
before SQL reaches a connector (parsing: executable comments; compose.checks.ensure_query:
SELECT ... INTO, row locks, LOAD_FILE / SLEEP / GET_LOCK ...), and the guarantee comes from
grants: connect as a user with only SELECT on the needed schemas and no FILE, PROCESS,
SUPER or other administrative privilege (see the README).

Timeout: inside that transaction every call runs ``SET SESSION max_execution_time = %s``
with the profile's ``statement_timeout_s`` in milliseconds as an int bound parameter
(PyMySQL escapes it client-side as a number). MySQL 8 then interrupts read-only SELECTs
with ER_QUERY_TIMEOUT (3024, "maximum statement execution time exceeded"), raised here as
``query timed out after Ns``. A ``MAX_EXECUTION_TIME`` / ``SET_VAR`` optimizer hint could
override it; parsing refuses SQL containing one. The socket ``read_timeout`` defaults to the
statement timeout + 30 s (a lost or stalled server, or a statement the server limit does
not cover, e.g. EXPLAIN); it closes the connection, and the next call reconnects.

Rows: an unbuffered cursor (SSCursor) fetches at most ``limit + 1`` rows, so wide results
are never held in memory. MySQL cannot stop sending a result, so closing the cursor reads
and discards the rest of the stream (cursors.py SSCursor.close →
``_finish_unbuffered_query``); the statement timeout bounds that too.

Column types come from ``information_schema.columns`` (``column_type``: full types such as
``tinyint(1)``, ``int unsigned``, ``enum('a','b')``) with bound parameters; in MySQL a schema
is a database, unqualified names resolve in ``DATABASE()``. ``explain`` runs ``EXPLAIN <sql>``.

Errors: PyMySQL raises ``(code, message)``; only the message is kept, ``'user'@'host'``
accounts in it are removed, then it is scrubbed as for every connector. PyMySQL has no
logger (only a module DEBUG flag that prints, off by default).
"""

from __future__ import annotations

import re
import ssl
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
    limited_result,
    load_driver,
    reject_plaintext_secrets,
    resolve_secrets,
    sensitive_values,
    split_table,
    timeout_error,
)

T = TypeVar("T")

TLS_OPTIONS = ("ssl_ca", "ssl_verify_cert", "ssl_verify_identity", "ssl_disabled")
ALLOWED_OPTIONS = frozenset(
    {"host", "port", "database", "user", "connect_timeout", "read_timeout", *TLS_OPTIONS}
)
ALLOWED_SECRETS = frozenset({"password"})
# Why the risky PyMySQL keywords are refused (every option outside the allowlist is).
REFUSED_OPTIONS = {
    "local_infile": "LOAD DATA LOCAL would let the server read files on this machine",
    "client_flag": "capability flags could enable multi-statements",
    "init_command": "the connector owns the session",
    "sql_mode": "the connector checks the server's sql_mode instead",
    "autocommit": "every call runs in its own READ ONLY transaction",
    "read_default_file": "credentials and hosts must not come from a my.cnf file",
    "read_default_group": "credentials and hosts must not come from a my.cnf file",
    "unix_socket": "connect over TCP with verified TLS",
    "charset": "the connection is always utf8mb4",
    "ssl": "use ssl_ca / ssl_verify_cert / ssl_verify_identity / ssl_disabled",
    "ssl_cert": "client certificates are not supported (password by env var only)",
    "ssl_key": "client certificates are not supported (password by env var only)",
}
CHARSET = "utf8mb4"
START_READ_ONLY_SQL = "START TRANSACTION READ ONLY"
# Milliseconds, an int bound parameter (escaped client-side by PyMySQL as a number).
STATEMENT_TIMEOUT_SQL = "SET SESSION max_execution_time = %s"
SESSION_MODE_SQL = "SELECT @@SESSION.sql_mode"
# sql_mode flags under which MySQL reads SQL differently from sqlglot's MySQL dialect.
REFUSED_SQL_MODES = frozenset({"NO_BACKSLASH_ESCAPES", "ANSI_QUOTES", "PIPES_AS_CONCAT"})
COLUMNS_SQL = (
    "SELECT table_schema, table_name, column_name, column_type "
    "FROM information_schema.columns "
    "WHERE LOWER(table_schema) = LOWER(%s) AND LOWER(table_name) = LOWER(%s) "
    "ORDER BY table_schema, table_name, ordinal_position"
)
CURRENT_DATABASE_COLUMNS_SQL = (
    "SELECT table_schema, table_name, column_name, column_type "
    "FROM information_schema.columns "
    "WHERE table_schema = DATABASE() AND LOWER(table_name) = LOWER(%s) "
    "ORDER BY table_schema, table_name, ordinal_position"
)
EXPLAIN_PREFIX = "EXPLAIN "
READ_TIMEOUT_MARGIN_S = 30
ER_QUERY_TIMEOUT = 3024  # MySQL max_execution_time exceeded.
ER_STATEMENT_TIMEOUT = 1969  # MariaDB max_statement_time exceeded (untested).
CR_SERVER_LOST = 2013  # "Lost connection to MySQL server during query (timed out)".
_TIMEOUT_TEXT = re.compile(r"maximum statement execution time exceeded", re.IGNORECASE)
_ACCOUNT = re.compile(r"'[^'\n]*'@'[^'\n]*'")  # 'user'@'client-host' in access errors


def _check_options(profile: WarehouseProfile) -> None:
    unknown = sorted(set(profile.options) - ALLOWED_OPTIONS)
    if unknown:
        reasons = "; ".join(f"{k}: {REFUSED_OPTIONS[k]}" for k in unknown if k in REFUSED_OPTIONS)
        raise WarehouseError(
            f"Profile '{profile.name}' sets options {unknown} the MySQL connector does not "
            f"accept; allowed: {sorted(ALLOWED_OPTIONS)}" + (f" ({reasons})" if reasons else "")
        )
    secrets = sorted(set(profile.secrets_env) - ALLOWED_SECRETS)
    if secrets:
        raise WarehouseError(
            f"Profile '{profile.name}' sets secrets_env {secrets}; the MySQL connector "
            "accepts only 'password'"
        )
    user = profile.options.get("user")
    if not isinstance(user, str) or not user:
        raise WarehouseError(
            f"Profile '{profile.name}' must set options.user to a dedicated read-only MySQL "
            "user (without it the driver logs in as the operating-system user)"
        )
    for name in ("ssl_verify_cert", "ssl_verify_identity", "ssl_disabled"):
        if name in profile.options and not isinstance(profile.options[name], bool):
            raise WarehouseError(f"Profile '{profile.name}': {name} must be true or false")
    if "ssl_ca" in profile.options and not isinstance(profile.options["ssl_ca"], str):
        raise WarehouseError(f"Profile '{profile.name}': ssl_ca must be a file path")
    if "port" in profile.options:
        _positive(profile, "port", profile.options["port"], integer=True)
    for name in ("connect_timeout", "read_timeout"):
        if name in profile.options:
            _positive(profile, name, profile.options[name])


def _positive(profile: WarehouseProfile, name: str, value: Any, *, integer: bool = False) -> None:
    kinds = (int,) if integer else (int, float)
    if isinstance(value, bool) or not isinstance(value, kinds) or value <= 0:
        kind = "integer" if integer else "number"
        raise WarehouseError(f"Profile '{profile.name}': {name} must be a positive {kind}")


def tls_context(options: dict[str, Any]) -> ssl.SSLContext | None:
    """The TLS context PyMySQL is given (None = TLS off, `ssl_disabled: true`). Verified by
    default: certificate against the system trust store (or `ssl_ca`) and host name."""
    if options.get("ssl_disabled") is True:
        return None
    verify_cert = options.get("ssl_verify_cert", True)
    verify_identity = options.get("ssl_verify_identity", True) and verify_cert
    context = ssl.create_default_context(cafile=options.get("ssl_ca"))
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = verify_identity
    context.verify_mode = ssl.CERT_REQUIRED if verify_cert else ssl.CERT_NONE
    return context


def driver_kwargs(
    profile: WarehouseProfile, secrets: dict[str, str], cursor_class: Any
) -> dict[str, Any]:
    """PyMySQL connect() keywords: the allowlisted options plus the connector's fixed ones."""
    options = connect_kwargs(profile, secrets)
    tls = {k: options.pop(k) for k in TLS_OPTIONS if k in options}
    if profile.statement_timeout_s is not None:
        options.setdefault("read_timeout", profile.statement_timeout_s + READ_TIMEOUT_MARGIN_S)
    context = tls_context(tls)
    options |= {"ssl": context} if context is not None else {"ssl_disabled": True}
    return options | {
        "charset": CHARSET,
        "autocommit": False,
        "local_infile": False,
        "client_flag": 0,  # PyMySQL adds its CAPABILITIES (no MULTI_STATEMENTS)
        "init_command": None,
        "sql_mode": None,
        "read_default_file": None,
        "cursorclass": cursor_class,  # unbuffered: rows are read as fetched
    }


def error_message(exc: BaseException) -> str:
    """A PyMySQL error's message: the server's text from `(code, message)` args, with any
    `'user'@'host'` account removed. Not yet scrubbed (Sensitive.scrub does that)."""
    args = getattr(exc, "args", ())
    text = args[1] if len(args) == 2 and isinstance(args[1], str) else str(exc)
    return _ACCOUNT.sub("***", text)


def _code(exc: BaseException) -> int | None:
    args = getattr(exc, "args", ())
    return args[0] if args and isinstance(args[0], int) and not isinstance(args[0], bool) else None


def is_mysql_timeout(exc: BaseException) -> bool:
    """max_execution_time (3024; MariaDB max_statement_time 1969), the socket read timeout
    (2013 "... (timed out)"), or any timeout `base.is_timeout` recognizes."""
    code = _code(exc)
    if code in (ER_QUERY_TIMEOUT, ER_STATEMENT_TIMEOUT):
        return True
    message = error_message(exc)
    if code == CR_SERVER_LOST and "timed out" in message:
        return True
    return bool(_TIMEOUT_TEXT.search(message)) or is_timeout(exc)


class MySQLWarehouse:
    dialect: Dialect = Dialect.MYSQL

    def __init__(self, profile: WarehouseProfile) -> None:
        driver = load_driver("pymysql", "mysql")
        cursors = load_driver("pymysql.cursors", "mysql")
        reject_plaintext_secrets(profile)
        _check_options(profile)
        secrets = resolve_secrets(profile)
        self._sensitive: Sensitive = sensitive_values(profile, secrets)
        self._max_rows = profile.max_rows
        self._timeout_s = profile.statement_timeout_s
        self._timeout_ms = None if self._timeout_s is None else int(self._timeout_s * 1000)
        self._closed = False
        try:
            kwargs = driver_kwargs(profile, secrets, cursors.SSCursor)
            self._con = driver.connect(**kwargs)
        except Exception as exc:
            raise self._error("Could not connect to MySQL", exc) from None
        try:
            self._call("Could not check the MySQL session", self._session_mode, check=False)
        except WarehouseError:
            self._close_quietly()
            raise

    # ── calls ──────────────────────────────────────────────────────────────────────

    def _error(self, context: str, exc: BaseException) -> WarehouseError:
        detail = self._sensitive.scrub(error_message(exc).strip()) or type(exc).__name__
        return WarehouseError(f"{context}: {detail}")

    def _call(self, context: str, action: Callable[[Any], T], *, check: bool = True) -> T:
        """Run `action(cursor)` in its own READ ONLY transaction with the statement timeout,
        then roll back. A dropped connection is reopened (and its session checked) first.
        Driver exceptions become scrubbed WarehouseErrors; timeouts `timeout_error`."""
        if self._closed:
            raise WarehouseError("The MySQL connection is closed")
        try:
            if check and not self._con.open:
                self._con.connect()
                self._transaction(self._session_mode)
            return self._transaction(action)
        except WarehouseError as exc:
            raise WarehouseError(self._sensitive.scrub(str(exc))) from None
        except Exception as exc:
            if self._timeout_s is not None and is_mysql_timeout(exc):
                raise timeout_error(self._timeout_s) from None
            raise self._error(context, exc) from None

    def _transaction(self, action: Callable[[Any], T]) -> T:
        cursor = self._con.cursor()
        try:
            cursor.execute(START_READ_ONLY_SQL)
            if self._timeout_ms is not None:
                cursor.execute(STATEMENT_TIMEOUT_SQL, (self._timeout_ms,))
            result = action(cursor)
        except BaseException:
            self._end(cursor, quiet=True)  # the original error wins
            raise
        self._end(cursor, quiet=False)
        return result

    def _end(self, cursor: Any, *, quiet: bool) -> None:
        """Close the cursor (an unbuffered one reads the rest of its result) and roll back
        (skipped when the connection has dropped: the server already ended the transaction)."""
        try:
            cursor.close()
        except Exception:
            if not quiet:
                raise
        try:
            if self._con.open:
                self._con.rollback()
        except Exception:
            if not quiet:
                raise

    def _session_mode(self, cursor: Any) -> None:
        cursor.execute(SESSION_MODE_SQL)
        row = cursor.fetchone()
        mode = row[0] if row else ""
        mode = mode.decode() if isinstance(mode, bytes) else str(mode or "")
        refused = sorted(REFUSED_SQL_MODES & {m.strip().upper() for m in mode.split(",")})
        if refused:
            raise WarehouseError(
                f"The MySQL session's sql_mode has {refused}; cteforge reads and writes MySQL "
                'SQL as under the default sql_mode (backslash escapes in strings, "..." '
                "as a string, || as OR). Remove these modes for the cteforge user."
            )

    def _close_quietly(self) -> None:
        try:
            self._con.close()
        except Exception:
            pass

    # ── Warehouse protocol ───────────────────────────────────────────────────────

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if not self._con.open:
            return
        try:
            self._con.close()
        except Exception as exc:
            raise self._error("Could not close connection", exc) from None

    def column_types(self, table: str) -> dict[str, str]:
        parts = split_table(table)
        if parts is None or parts[0] is not None:  # MySQL names are [database.]table
            return {}
        _, database, name = parts
        if database is None:
            sql, params = CURRENT_DATABASE_COLUMNS_SQL, (name,)
        else:
            sql, params = COLUMNS_SQL, (database, name)

        def action(cursor: Any) -> list[Any]:
            cursor.execute(sql, params)
            return list(cursor.fetchall())

        rows = self._call("Schema lookup failed", action)
        return _columns_of_one_table(rows, database, name)

    def explain(self, sql: str) -> None:
        def action(cursor: Any) -> None:
            cursor.execute(EXPLAIN_PREFIX + sql)
            cursor.fetchall()

        self._call("Query does not compile", action)

    def run(self, sql: str, limit: int) -> QueryResult:
        limit = effective_limit(limit, self._max_rows)

        def action(cursor: Any) -> QueryResult:
            cursor.execute(sql)  # no arguments: sent unchanged, `%` is not interpreted
            return limited_result(cursor, limit)

        return self._call("Query failed", action)


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _columns_of_one_table(rows: list[Any], database: str | None, name: str) -> dict[str, str]:
    """Rows (schema, table, column, type) → column (lower-case) → type for ONE table: the
    exact-case match when there is one, else the only case-insensitive match; empty when
    ambiguous or unknown (MySQL table names are case-sensitive where lower_case_table_names
    is 0; the Warehouse protocol is not)."""
    rows = [tuple(_text(v) for v in r) for r in rows]
    exact = [r for r in rows if r[1] == name and (database is None or r[0] == database)]
    chosen = exact or rows
    if len({(r[0], r[1]) for r in chosen}) != 1:
        return {}
    columns: dict[str, str] = {}
    for _schema, _table, column, dtype in chosen:
        columns.setdefault(column.lower(), dtype)
    return columns
