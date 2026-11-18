"""Warehouse connectors (HARDENED — read CLAUDE.md here before editing).

Each connector is constructible from a WarehouseProfile; non-DuckDB drivers are imported
lazily, so importing this package never needs an optional extra.
"""

from cteforge.warehouse.base import QueryResult, Warehouse
from cteforge.warehouse.bigquery import BigQueryWarehouse
from cteforge.warehouse.clickhouse import ClickHouseWarehouse
from cteforge.warehouse.databricks import DatabricksWarehouse
from cteforge.warehouse.duckdb import DuckDBWarehouse
from cteforge.warehouse.mysql import MySQLWarehouse
from cteforge.warehouse.postgres import PostgresWarehouse
from cteforge.warehouse.redshift import RedshiftWarehouse
from cteforge.warehouse.snowflake import SnowflakeWarehouse

__all__ = [
    "BigQueryWarehouse",
    "ClickHouseWarehouse",
    "DatabricksWarehouse",
    "DuckDBWarehouse",
    "MySQLWarehouse",
    "PostgresWarehouse",
    "QueryResult",
    "RedshiftWarehouse",
    "SnowflakeWarehouse",
    "Warehouse",
]
