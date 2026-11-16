"""Open warehouse adapters for entry points (which may not import warehouse/ directly)."""

from __future__ import annotations

from pathlib import Path

from cteforge.domain.dialects import Dialect
from cteforge.warehouse.base import Warehouse
from cteforge.warehouse.duckdb import DuckDBWarehouse


def open_warehouse(path: str | Path) -> tuple[Warehouse, Dialect]:
    """M1 supports DuckDB files only. Returns the warehouse and its dialect."""
    return DuckDBWarehouse(path), Dialect.DUCKDB
