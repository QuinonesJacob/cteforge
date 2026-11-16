"""Databricks SQL connector via databricks-sql-connector (HARDENED module — read CLAUDE.md
here before editing). Install with ``pip install 'cteforge[databricks]'``.

Profile ``options``: server_hostname, http_path, optional catalog and schema. Secrets are
referenced by env var, e.g. ``secrets_env: {access_token: DATABRICKS_TOKEN}``.

Read-only: Databricks has no read-only session mode. Use a service principal whose token
only has ``USE CATALOG``, ``USE SCHEMA`` and ``SELECT`` grants (Unity Catalog) and
CAN USE on the SQL warehouse.

Single statement: Databricks SQL parses one statement per ``execute`` call, so a
``;``-separated string is a syntax error (server behaviour; not re-checked here, and not
yet validated against a live warehouse). Services refuse non-SELECT SQL before ``run``.

Column types come from the driver metadata API (``cursor.columns``), not from SQL text.
EXPLAIN reports planning errors as plan text instead of raising, so it is inspected.
"""

from __future__ import annotations

from typing import Any

from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import WarehouseError
from cteforge.warehouse.base import (
    DbApiSession,
    QueryResult,
    connect_kwargs,
    driver_error,
    effective_limit,
    load_driver,
    resolve_secrets,
    sensitive_values,
    split_table,
)

CURRENT_NAMESPACE_SQL = "SELECT current_catalog(), current_schema()"
_PLAN_ERROR_MARKERS = ("error occurred during query planning", "analysisexception")


class DatabricksWarehouse:
    dialect: Dialect = Dialect.DATABRICKS

    def __init__(self, profile: WarehouseProfile) -> None:
        driver = load_driver("databricks.sql", "databricks")
        secrets = resolve_secrets(profile)
        sensitive = sensitive_values(profile, secrets)
        self._max_rows = profile.max_rows
        try:
            con = driver.connect(**connect_kwargs(profile, secrets))
        except Exception as exc:
            raise driver_error("Could not connect to Databricks", exc, sensitive) from None
        self._session = DbApiSession(con, sensitive)

    def close(self) -> None:
        self._session.close()

    def column_types(self, table: str) -> dict[str, str]:
        parts = split_table(table)
        if parts is None:
            return {}
        target = tuple(p.lower() if p else None for p in parts)

        def action(cursor: Any) -> dict[str, str]:
            catalog, schema, name = target
            if catalog is None or schema is None:
                cursor.execute(CURRENT_NAMESPACE_SQL)
                current_catalog, current_schema = cursor.fetchone()
                catalog = catalog or current_catalog
                schema = schema or current_schema
            cursor.columns(catalog_name=catalog, schema_name=schema, table_name=name)
            return _matching_columns(cursor, (catalog, schema, name))

        return self._session.call("Schema lookup failed", action)

    def explain(self, sql: str) -> None:
        def action(cursor: Any) -> None:
            cursor.execute("EXPLAIN " + sql)
            _raise_on_plan_error(cursor.fetchall())

        self._session.call("Query does not compile", action)

    def run(self, sql: str, limit: int) -> QueryResult:
        return self._session.limited(sql, effective_limit(limit, self._max_rows))


def _matching_columns(cursor: Any, target: tuple[Any, Any, str]) -> dict[str, str]:
    """Keep exact (case-insensitive) matches: metadata names are LIKE patterns."""
    index = {d[0].upper(): i for i, d in enumerate(cursor.description or [])}
    keys = ("TABLE_CAT", "TABLE_SCHEM", "TABLE_NAME")
    result: dict[str, str] = {}
    for row in cursor.fetchall():
        found = tuple(str(row[index[k]]).lower() for k in keys)
        if found == tuple(str(t).lower() for t in target):
            result[str(row[index["COLUMN_NAME"]]).lower()] = str(row[index["TYPE_NAME"]])
    return result


def _raise_on_plan_error(rows: list[Any]) -> None:
    plan = "\n".join(str(r[0]) for r in rows if r)
    if any(marker in plan.lower() for marker in _PLAN_ERROR_MARKERS):
        raise WarehouseError(f"Query does not compile: {plan.strip()}")
