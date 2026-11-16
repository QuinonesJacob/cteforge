"""Postgres / Redshift connector via psycopg 3 (HARDENED module — read CLAUDE.md here before
editing). Install with ``pip install 'cteforge[postgres]'``.

Profile ``options`` are psycopg connection keywords (host, port, dbname or database, user,
sslmode, ...). Secrets are referenced by env var, e.g. ``secrets_env: {password: PG_PASSWORD}``.

Single statement: every statement is sent with ``prepare=True``, i.e. over the extended
query protocol, which refuses strings holding more than one statement. (Without bind
parameters psycopg would otherwise use the simple protocol, which runs them all.)

Read-only: the connection is not in autocommit mode and has ``read_only = True``, so psycopg
opens every transaction with ``BEGIN READ ONLY``; every call ends with a rollback. A
statement cannot switch its own transaction to read-write once it has started, and
session settings it changes (``set_config(...)``) do not affect the next ``BEGIN READ
ONLY``. This is defence in depth, not the guarantee: functions with side effects (e.g.
dblink) are only stopped by grants. Always connect as a dedicated read-only role, e.g.
``CREATE ROLE cteforge_ro LOGIN; GRANT USAGE ON SCHEMA s TO cteforge_ro; GRANT SELECT ON
ALL TABLES IN SCHEMA s TO cteforge_ro;`` (Redshift: a user with only SELECT grants).
Connection poolers in transaction mode (pgbouncer) need prepared-statement support.
"""

from __future__ import annotations

from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.dialects import Dialect
from cteforge.warehouse.base import (
    DbApiSession,
    QueryResult,
    columns_query,
    connect_kwargs,
    driver_error,
    effective_limit,
    load_driver,
    resolve_secrets,
    sensitive_values,
    split_table,
)

# Extended query protocol for every statement: refuses multi-statement strings.
EXECUTE_OPTIONS = {"prepare": True}
_CURRENT_SCHEMA = "current_schema()"


class PostgresWarehouse:
    dialect: Dialect = Dialect.POSTGRES

    def __init__(self, profile: WarehouseProfile) -> None:
        driver = load_driver("psycopg", "postgres")
        secrets = resolve_secrets(profile)
        sensitive = sensitive_values(profile, secrets)
        kwargs = connect_kwargs(profile, secrets)
        if "database" in kwargs:
            kwargs.setdefault("dbname", kwargs.pop("database"))
        kwargs["autocommit"] = False  # transactions are needed for BEGIN READ ONLY
        self._max_rows = profile.max_rows
        try:
            con = driver.connect(**kwargs)
        except Exception as exc:
            raise driver_error("Could not connect to Postgres", exc, sensitive) from None
        try:
            con.read_only = True
        except Exception as exc:
            con.close()
            raise driver_error("Could not make session read-only", exc, sensitive) from None
        self._session = DbApiSession(
            con, sensitive, execute_options=EXECUTE_OPTIONS, rollback_after=True
        )

    def close(self) -> None:
        self._session.close()

    def column_types(self, table: str) -> dict[str, str]:
        parts = split_table(table)
        if parts is None:
            return {}
        sql, params = columns_query(parts, "%s", _CURRENT_SCHEMA)
        rows = self._session.rows("Schema lookup failed", sql, params)
        return {col: dtype for col, dtype in rows}

    def explain(self, sql: str) -> None:
        self._session.rows("Query does not compile", "EXPLAIN " + sql)

    def run(self, sql: str, limit: int) -> QueryResult:
        return self._session.limited(sql, effective_limit(limit, self._max_rows))
