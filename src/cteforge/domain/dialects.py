"""Supported SQL dialects (names match sqlglot dialect names)."""

from enum import StrEnum


class Dialect(StrEnum):
    DUCKDB = "duckdb"
    POSTGRES = "postgres"
    REDSHIFT = "redshift"
    SNOWFLAKE = "snowflake"
    BIGQUERY = "bigquery"
    DATABRICKS = "databricks"
    CLICKHOUSE = "clickhouse"
    MYSQL = "mysql"
