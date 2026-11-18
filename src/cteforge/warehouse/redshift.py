"""Amazon Redshift connector via redshift_connector, AWS's Python driver, Apache-2.0
(HARDENED module — read CLAUDE.md here before editing). Install with
``pip install 'cteforge[redshift]'``. ADR 0022.

Why not psycopg (the ``postgres`` extra): psycopg 3 does not support Redshift officially,
and the Postgres connector's single-statement guarantee (named prepared statements) makes
psycopg send ``DEALLOCATE ALL`` after every rollback, which Redshift does not have. Without
prepared statements psycopg sends parameterless SQL over the simple query protocol, which
runs every statement in a string. redshift_connector always uses the extended protocol.

Options: only those in ``ALLOWED_OPTIONS`` (host, port, database or dbname, user, ssl,
sslmode, timeout, application_name, tcp_keepalive*). Others are refused by name: IAM / IdP
authentication options would let the driver (boto3) read AWS credentials from the
environment and ``~/.aws`` on its own, outside ``secrets_env``; that needs its own security
review. The password is referenced by env var: ``secrets_env: {password: RS_PASSWORD}``.
The driver verifies TLS by default (``ssl: true``, ``sslmode: verify-full``).

Single statement: the driver sends every statement as Parse/Bind/Execute (extended query
protocol), and Redshift refuses a Parse holding more than one statement ("cannot insert
multiple commands into a prepared statement").

Read-only: the connection is in autocommit mode so the driver never opens a read-write
``begin transaction`` itself; every call opens its own ``BEGIN READ ONLY`` transaction
(Redshift's BEGIN documents READ ONLY) and ends with a rollback. That is defence in depth,
not the guarantee, which comes from grants: connect as a user with only USAGE on the
schemas and SELECT on the tables (see the README).

Timeout: inside that transaction every call runs ``SELECT set_config('statement_timeout',
<ms>, true)`` (transaction-local; the value is a bind parameter). Redshift's
statement_timeout counts planning, WLM queueing and execution; when a WLM timeout is also
set the lower one applies. A cancelled statement raises ``query timed out after Ns``.

Column types come from ``svv_columns``, which (unlike information_schema.columns) also
lists late-binding views and external (Spectrum) tables. Unqualified names resolve in
``current_schema()``, fetched first as its own statement.

The driver's DEBUG log lines name the host and echo statements and server errors; its
logger is set to WARNING unless the operator set a level for it.
"""

from __future__ import annotations

import logging
from typing import Any

from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import WarehouseError
from cteforge.warehouse.base import (
    DbApiSession,
    QueryResult,
    columns_query,
    connect_kwargs,
    driver_error,
    effective_limit,
    load_driver,
    reject_plaintext_secrets,
    resolve_secrets,
    sensitive_values,
    split_table,
)

ALLOWED_OPTIONS = frozenset(
    {
        "host",
        "port",
        "database",
        "dbname",
        "user",
        "ssl",
        "sslmode",
        "timeout",
        "application_name",
        "tcp_keepalive",
        "tcp_keepalive_idle",
        "tcp_keepalive_interval",
        "tcp_keepalive_count",
    }
)
ALLOWED_SECRETS = frozenset({"password"})
BEGIN_READ_ONLY_SQL = "BEGIN READ ONLY"
# Transaction-local (is_local = true) like SET LOCAL; SET itself takes no bind parameters.
STATEMENT_TIMEOUT_SQL = "SELECT set_config('statement_timeout', %s, true)"
CURRENT_SCHEMA_SQL = "SELECT current_schema()"
_COLUMNS_VIEW = "svv_columns"
_DRIVER_LOGGER = "redshift_connector"


def _check_options(profile: WarehouseProfile) -> None:
    unknown = sorted(set(profile.options) - ALLOWED_OPTIONS)
    if unknown:
        raise WarehouseError(
            f"Profile '{profile.name}' sets options {unknown} the Redshift connector does not "
            f"accept; allowed: {sorted(ALLOWED_OPTIONS)} (IAM/IdP authentication is not "
            "supported yet)"
        )
    secrets = sorted(set(profile.secrets_env) - ALLOWED_SECRETS)
    if secrets:
        raise WarehouseError(
            f"Profile '{profile.name}' sets secrets_env {secrets}; the Redshift connector "
            "accepts only 'password'"
        )


def _quiet_driver_logs() -> None:
    logger = logging.getLogger(_DRIVER_LOGGER)
    if logger.level == logging.NOTSET:
        logger.setLevel(logging.WARNING)


class RedshiftWarehouse:
    dialect: Dialect = Dialect.REDSHIFT

    def __init__(self, profile: WarehouseProfile) -> None:
        driver = load_driver("redshift_connector", "redshift")
        reject_plaintext_secrets(profile)
        _check_options(profile)
        secrets = resolve_secrets(profile)
        sensitive = sensitive_values(profile, secrets)
        kwargs = connect_kwargs(profile, secrets)
        if "dbname" in kwargs:
            kwargs.setdefault("database", kwargs.pop("dbname"))
        self._max_rows = profile.max_rows
        _quiet_driver_logs()
        try:
            con = driver.connect(**kwargs)
        except Exception as exc:
            raise driver_error("Could not connect to Redshift", exc, sensitive) from None
        try:
            # The driver would otherwise open a read-write "begin transaction" before the
            # first statement; each call opens BEGIN READ ONLY itself instead (prelude).
            con.autocommit = True
        except Exception as exc:
            con.close()
            raise driver_error("Could not configure the session", exc, sensitive) from None
        timeout_s = profile.statement_timeout_s
        self._timeout_ms = None if timeout_s is None else str(timeout_s * 1000)
        self._session = DbApiSession(
            con,
            sensitive,
            rollback_after=True,
            prelude=self._begin,
            timeout_s=timeout_s,
        )

    def _begin(self, cursor: Any) -> None:
        """Runs first in every call (DbApiSession prelude): a read-only transaction with
        the profile's transaction-local statement timeout."""
        self._session.execute(cursor, BEGIN_READ_ONLY_SQL)
        if self._timeout_ms is not None:
            self._session.execute(cursor, STATEMENT_TIMEOUT_SQL, [self._timeout_ms])

    def close(self) -> None:
        self._session.close()

    def column_types(self, table: str) -> dict[str, str]:
        parts = split_table(table)
        if parts is None:
            return {}
        catalog, schema, name = parts

        def action(cursor: Any) -> dict[str, str]:
            resolved = schema
            if resolved is None:
                self._session.execute(cursor, CURRENT_SCHEMA_SQL)
                row = cursor.fetchone()
                resolved = row[0] if row else None
                if not resolved:
                    return {}
            sql, params = columns_query((catalog, resolved, name), "%s", "", _COLUMNS_VIEW)
            self._session.execute(cursor, sql, params)
            return {col: dtype for col, dtype in cursor.fetchall()}

        return self._session.call("Schema lookup failed", action)

    def explain(self, sql: str) -> None:
        self._session.rows("Query does not compile", "EXPLAIN " + sql)

    def run(self, sql: str, limit: int) -> QueryResult:
        return self._session.limited(sql, effective_limit(limit, self._max_rows))
