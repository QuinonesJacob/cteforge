"""DuckDB connector (HARDENED module — read CLAUDE.md here before editing).

Opens the database file read-only with external access disabled (no file, network or
extension access; configuration locked), so no role setup is needed. Read-only mode alone
still allows ``COPY ... TO`` files and temporary tables, so ``explain`` and ``run`` first
parse the SQL with DuckDB's own parser and accept exactly one SELECT statement.
Executes SQL produced by templating/ as-is; never builds SQL from values. Table names are
passed as query parameters.

Profile: ``options.path`` = the DuckDB file. No secrets.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import WarehouseError
from cteforge.warehouse.base import (
    QueryResult,
    columns_query,
    effective_limit,
    limited_result,
    split_table,
)

MAX_ROWS = 10_000

# Unqualified names resolve in the current schema, as they would in a query.
_CURRENT_SCHEMA = "current_schema()"
# No files, network or extension installs; settings cannot be changed afterwards.
_CONFIG = {"enable_external_access": False, "lock_configuration": True}


class DuckDBWarehouse:
    dialect: Dialect = Dialect.DUCKDB

    def __init__(self, path: str | Path, max_rows: int = MAX_ROWS) -> None:
        db_path = Path(path)
        if not db_path.exists():
            raise WarehouseError(f"DuckDB file not found: {db_path.name}")
        self._max_rows = max_rows
        try:
            self._con = duckdb.connect(str(db_path), read_only=True, config=dict(_CONFIG))
        except duckdb.Error as exc:
            raise WarehouseError(f"Could not open DuckDB database: {type(exc).__name__}") from None

    @classmethod
    def from_profile(cls, profile: WarehouseProfile) -> DuckDBWarehouse:
        path = profile.options.get("path")
        if not isinstance(path, str) or not path:
            raise WarehouseError(f"Profile '{profile.name}' needs options.path (DuckDB file)")
        return cls(path, max_rows=profile.max_rows)

    def close(self) -> None:
        self._con.close()

    def column_types(self, table: str) -> dict[str, str]:
        parts = split_table(table)
        if parts is None:
            return {}
        query, params = columns_query(parts, "?", _CURRENT_SCHEMA)
        try:
            rows = self._con.execute(query, params).fetchall()
        except duckdb.Error as exc:
            raise WarehouseError(f"Schema lookup failed: {exc}") from None
        return {col: dtype for col, dtype in rows}

    def explain(self, sql: str) -> None:
        try:
            self._require_single_select(sql)
            self._con.execute("EXPLAIN " + sql).fetchall()
        except duckdb.Error as exc:
            raise WarehouseError(f"Query does not compile: {exc}") from None

    def run(self, sql: str, limit: int) -> QueryResult:
        limit = effective_limit(limit, self._max_rows)
        try:
            self._require_single_select(sql)
            return limited_result(self._con.execute(sql), limit)
        except duckdb.Error as exc:
            raise WarehouseError(f"Query failed: {exc}") from None

    def _require_single_select(self, sql: str) -> None:
        """Refuse anything but exactly one SELECT, as DuckDB itself parses the string."""
        statements = self._con.extract_statements(sql)
        if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
            kinds = ", ".join(s.type.name for s in statements) or "none"
            raise WarehouseError(f"Only a single SELECT statement is allowed (got: {kinds})")
