"""Snowflake connector via snowflake-connector-python (HARDENED module — read CLAUDE.md here
before editing). Install with ``pip install 'cteforge[snowflake]'``.

Profile ``options`` are connector keywords (account, user, warehouse, database, schema, role,
authenticator, ...). Secrets are referenced by env var, e.g.
``secrets_env: {password: SNOWFLAKE_PASSWORD}`` or ``{private_key_file_pwd: SF_KEY_PWD}``.

Read-only: Snowflake has no read-only session mode. Set ``options.role`` to a role that only
has USAGE on the warehouse/database/schemas and SELECT on the tables, e.g.
``GRANT SELECT ON ALL TABLES IN SCHEMA db.s TO ROLE cteforge_ro``. EXPLAIN only compiles.

Single statement: every execute passes ``num_statements=1`` (MULTI_STATEMENT_COUNT), so
Snowflake refuses a string holding more than one statement whatever the account default.

Timeout: the profile's ``statement_timeout_s`` is set as the session parameter
``STATEMENT_TIMEOUT_IN_SECONDS`` at connect time (server-side cancel); a cancelled
statement (SQLSTATE 57014) raises ``query timed out after Ns``.
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

# information_schema only covers the current database; 3-part names in another database
# return no columns.
_CURRENT_SCHEMA = "CURRENT_SCHEMA()"
# Snowflake refuses strings that hold a different number of statements.
EXECUTE_OPTIONS = {"num_statements": 1}


class SnowflakeWarehouse:
    dialect: Dialect = Dialect.SNOWFLAKE

    def __init__(self, profile: WarehouseProfile) -> None:
        driver = load_driver("snowflake.connector", "snowflake")
        secrets = resolve_secrets(profile)
        sensitive = sensitive_values(profile, secrets)
        self._max_rows = profile.max_rows
        kwargs = connect_kwargs(profile, secrets)
        timeout_s = profile.statement_timeout_s
        if timeout_s is not None:
            kwargs["session_parameters"] = {"STATEMENT_TIMEOUT_IN_SECONDS": timeout_s}
        try:
            con = driver.connect(**kwargs)
        except Exception as exc:
            raise driver_error("Could not connect to Snowflake", exc, sensitive) from None
        self._session = DbApiSession(
            con, sensitive, execute_options=EXECUTE_OPTIONS, timeout_s=timeout_s
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
        self._session.rows("Query does not compile", "EXPLAIN USING TEXT " + sql)

    def run(self, sql: str, limit: int) -> QueryResult:
        return self._session.limited(sql, effective_limit(limit, self._max_rows))
