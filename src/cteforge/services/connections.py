"""Warehouse registry: named profiles → lazily opened warehouses (ADR 0020).

Entry points may not import warehouse/ directly; they go through this module. Public
summaries carry only a profile's name and dialect, never options or secret references.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import CodecError, NotFoundError, PolicyError, WarehouseError
from cteforge.warehouse.base import QueryResult, Warehouse
from cteforge.warehouse.bigquery import BigQueryWarehouse
from cteforge.warehouse.databricks import DatabricksWarehouse
from cteforge.warehouse.duckdb import DuckDBWarehouse
from cteforge.warehouse.postgres import PostgresWarehouse
from cteforge.warehouse.snowflake import SnowflakeWarehouse

Opener = Callable[[WarehouseProfile], Warehouse]

# Connectors import their drivers lazily, so importing them here costs nothing.
OPENERS: dict[Dialect, Opener] = {
    Dialect.DUCKDB: DuckDBWarehouse.from_profile,
    Dialect.POSTGRES: PostgresWarehouse,
    Dialect.SNOWFLAKE: SnowflakeWarehouse,
    Dialect.BIGQUERY: BigQueryWarehouse,
    Dialect.DATABRICKS: DatabricksWarehouse,
}


def open_warehouse(path: str | Path) -> tuple[Warehouse, Dialect]:
    """M1 CLI: open a DuckDB file directly. Returns the warehouse and its dialect."""
    return DuckDBWarehouse(path), Dialect.DUCKDB


def open_profile(profile: WarehouseProfile) -> Warehouse:
    """Open a warehouse for a profile, dispatching on its dialect."""
    opener = OPENERS.get(profile.dialect)
    if opener is None:  # pragma: no cover - every Dialect has a connector
        raise WarehouseError(f"No connector for dialect {profile.dialect}")
    return opener(profile)


BUSY_TIMEOUT_SECONDS = 30.0  # Longest a call waits for a warehouse another call is using.
CLOSE_TIMEOUT_SECONDS = 5.0  # Longest close() waits for a running call before giving up.


class SerializedWarehouse:
    """A Warehouse whose calls run one at a time (Warehouse protocol).

    Connectors hold ONE driver connection (DuckDB's `DuckDBPyConnection`, a psycopg /
    Snowflake / Databricks connection) and run `execute` then a fetch on it, so two threads
    using it at once could read each other's result sets. The registry hands every caller
    this wrapper: each `column_types` / `explain` / `run` holds the warehouse's lock from
    execute to fetch. Other attributes are passed through to the connector.

    Waiting is bounded: a call that cannot get the lock within `busy_timeout` seconds
    raises WarehouseError ("busy") instead of queueing behind a long query, so one slow
    query cannot pile up every other caller's worker thread. `close()` waits at most
    `close_timeout` seconds; when a call still holds the connection it is left open (the
    process is shutting down) rather than hanging. The query that holds the lock is not
    cancelled here: each connector enforces the profile's `statement_timeout_s` itself
    (warehouse/), which bounds how long the lock is held where the driver supports it.
    """

    def __init__(
        self,
        inner: Warehouse,
        *,
        busy_timeout: float = BUSY_TIMEOUT_SECONDS,
        close_timeout: float = CLOSE_TIMEOUT_SECONDS,
    ) -> None:
        self._inner = inner
        self._lock = threading.Lock()
        self._busy_timeout = busy_timeout
        self._close_timeout = close_timeout
        self.dialect: Dialect = inner.dialect

    @property
    def inner(self) -> Warehouse:
        return self._inner

    @contextmanager
    def _turn(self) -> Iterator[None]:
        if not self._lock.acquire(timeout=self._busy_timeout):
            raise WarehouseError(
                f"The warehouse is busy with another query (waited {self._busy_timeout:g}s); "
                "try again shortly, or narrow long-running queries"
            )
        try:
            yield
        finally:
            self._lock.release()

    def column_types(self, table: str) -> dict[str, str]:
        with self._turn():
            return self._inner.column_types(table)

    def explain(self, sql: str) -> None:
        with self._turn():
            self._inner.explain(sql)

    def run(self, sql: str, limit: int) -> QueryResult:
        with self._turn():
            return self._inner.run(sql, limit)

    def close(self) -> None:
        if not self._lock.acquire(timeout=self._close_timeout):
            return  # A call still runs on the connection; closing under it is unsafe.
        try:
            self._inner.close()
        finally:
            self._lock.release()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class ProfileSummary(BaseModel):
    """What callers may see about a profile: no options, no secret references."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    dialect: Dialect


class WarehouseRegistry:
    """Profile name → warehouse, opened on first use and cached until `close_all`.

    Default profile: `default` if given, else the only profile when exactly one exists.
    Thread-safe: opening is locked, and every opened warehouse is wrapped in a
    `SerializedWarehouse`, so concurrent callers never share a result set on the one
    driver connection. A warehouse that fails to open is not cached (the next call
    retries).
    """

    def __init__(
        self,
        profiles: Iterable[WarehouseProfile] = (),
        *,
        default: str | None = None,
        opener: Opener = open_profile,
        busy_timeout: float = BUSY_TIMEOUT_SECONDS,
    ) -> None:
        self._busy_timeout = busy_timeout
        self._profiles: dict[str, WarehouseProfile] = {}
        for profile in profiles:
            if profile.name in self._profiles:
                raise CodecError(f"Warehouse profile '{profile.name}' is defined twice")
            self._profiles[profile.name] = profile
        if default is not None and default not in self._profiles:
            raise NotFoundError(
                f"Default warehouse profile '{default}' is not configured "
                f"(configured: {self.names() or 'none'})"
            )
        self._default = default
        self._opener = opener
        self._open: dict[str, SerializedWarehouse] = {}
        self._lock = threading.Lock()

    def names(self) -> list[str]:
        return list(self._profiles)

    def summaries(self) -> list[ProfileSummary]:
        return [ProfileSummary(name=p.name, dialect=p.dialect) for p in self._profiles.values()]

    @property
    def default_name(self) -> str | None:
        if self._default is not None:
            return self._default
        return next(iter(self._profiles)) if len(self._profiles) == 1 else None

    def resolve_name(self, name: str | None = None) -> str:
        """The profile `name`, or the default one. Raises NotFoundError / PolicyError."""
        if name is not None:
            if name not in self._profiles:
                raise NotFoundError(
                    f"Warehouse profile '{name}' is not configured "
                    f"(configured: {self.names() or 'none'})"
                )
            return name
        if not self._profiles:
            raise PolicyError(
                "No warehouse profiles are configured; set CTEFORGE_CONNECTIONS to a "
                "connections.yml file"
            )
        default = self.default_name
        if default is None:
            raise PolicyError(
                f"Several warehouse profiles are configured ({self.names()}); name one "
                "or set CTEFORGE_DEFAULT_PROFILE"
            )
        return default

    def profile(self, name: str | None = None) -> WarehouseProfile:
        return self._profiles[self.resolve_name(name)]

    def summary(self, name: str | None = None) -> ProfileSummary:
        p = self.profile(name)
        return ProfileSummary(name=p.name, dialect=p.dialect)

    def get(self, name: str | None = None) -> Warehouse:
        """The opened warehouse for `name` (or the default profile)."""
        key = self.resolve_name(name)
        with self._lock:
            warehouse = self._open.get(key)
            if warehouse is None:
                warehouse = SerializedWarehouse(
                    self._opener(self._profiles[key]), busy_timeout=self._busy_timeout
                )
                self._open[key] = warehouse
            return warehouse

    def is_open(self, name: str) -> bool:
        with self._lock:
            return name in self._open

    def close_all(self) -> None:
        """Close every opened warehouse; keep going if one fails. Safe to call twice."""
        with self._lock:
            opened, self._open = list(self._open.values()), {}
        for warehouse in opened:
            try:
                warehouse.close()
            except Exception:  # noqa: BLE001 - closing must not stop the others
                continue
