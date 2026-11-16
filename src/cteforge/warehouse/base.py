"""Warehouse protocol (HARDENED module — read CLAUDE.md here before editing)."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict


class QueryResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    columns: list[str]
    rows: list[tuple[Any, ...]]
    truncated: bool  # True if more rows existed than the limit.


class Warehouse(Protocol):
    def column_types(self, table: str) -> dict[str, str]:
        """Column name (lower-case) → warehouse type string. Empty if table unknown."""
        ...

    def explain(self, sql: str) -> None:
        """Raise WarehouseError if the SQL does not compile."""
        ...

    def run(self, sql: str, limit: int) -> QueryResult:
        """Execute read-only SQL, returning at most `limit` rows."""
        ...
