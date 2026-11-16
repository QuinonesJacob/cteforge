"""DuckDB connector (HARDENED module — read CLAUDE.md here before editing).

Opens the database read-only. Executes SQL produced by templating/ as-is; never builds
SQL from values. Table names are passed as query parameters.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from cteforge.domain.errors import WarehouseError
from cteforge.warehouse.base import QueryResult

MAX_ROWS = 10_000


class DuckDBWarehouse:
    def __init__(self, path: str | Path) -> None:
        db_path = Path(path)
        if not db_path.exists():
            raise WarehouseError(f"DuckDB file not found: {db_path.name}")
        try:
            self._con = duckdb.connect(str(db_path), read_only=True)
        except duckdb.Error as exc:
            raise WarehouseError(f"Could not open DuckDB database: {type(exc).__name__}") from None

    def close(self) -> None:
        self._con.close()

    def column_types(self, table: str) -> dict[str, str]:
        schema, _, name = table.rpartition(".")
        query = (
            "SELECT lower(column_name), data_type FROM information_schema.columns "
            "WHERE lower(table_name) = lower(?)"
        )
        params: list[str] = [name]
        if schema:
            query += " AND lower(table_schema) = lower(?)"
            params.append(schema)
        try:
            rows = self._con.execute(query, params).fetchall()
        except duckdb.Error as exc:
            raise WarehouseError(f"Schema lookup failed: {exc}") from None
        return {col: dtype for col, dtype in rows}

    def explain(self, sql: str) -> None:
        try:
            self._con.execute("EXPLAIN " + sql).fetchall()
        except duckdb.Error as exc:
            raise WarehouseError(f"Query does not compile: {exc}") from None

    def run(self, sql: str, limit: int) -> QueryResult:
        limit = max(0, min(limit, MAX_ROWS))
        try:
            cursor = self._con.execute(sql)
            rows = cursor.fetchmany(limit + 1)
            columns = [d[0] for d in cursor.description or []]
        except duckdb.Error as exc:
            raise WarehouseError(f"Query failed: {exc}") from None
        return QueryResult(
            columns=columns,
            rows=[tuple(r) for r in rows[:limit]],
            truncated=len(rows) > limit,
        )
