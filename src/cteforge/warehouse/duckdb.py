"""DuckDB connector (HARDENED module — read CLAUDE.md here before editing).

Opens the database file read-only with external access disabled (no file, network or
extension access; configuration locked), so no role setup is needed. Read-only mode alone
still allows ``COPY ... TO`` files and temporary tables, so ``explain`` and ``run`` first
parse the SQL with DuckDB's own parser and accept exactly one SELECT statement.
Executes SQL produced by templating/ as-is; never builds SQL from values. Table names are
passed as query parameters.

Profile: ``options.path`` = the DuckDB file. No secrets.

Errors: driver messages can quote file paths (a ``read_csv('/home/me/...')`` refused by the
configuration, the database file itself), which reveal the host's home directory and OS
user. Every message is passed through ``_scrub_host``: the database path becomes its file
name, its directory ``<warehouse dir>``, the home directory ``~``, the OS user name ``***``.

Timeout: with ``statement_timeout_s``, a timer thread calls ``connection.interrupt()``
when a call (``explain``/``run``, fetch included) runs longer; the interrupted call raises
``query timed out after Ns``. A timer that fires after the call finished does nothing.
"""

from __future__ import annotations

import getpass
import os
import re
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import duckdb

from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import WarehouseError
from cteforge.warehouse.base import (
    REDACTED,
    QueryResult,
    columns_query,
    effective_limit,
    limited_result,
    split_table,
    timeout_error,
)

MAX_ROWS = 10_000
DEFAULT_STATEMENT_TIMEOUT_S = 60
_MIN_USER_LENGTH = 3  # shorter OS user names would garble unrelated words
T = TypeVar("T")

# Unqualified names resolve in the current schema, as they would in a query.
_CURRENT_SCHEMA = "current_schema()"
# No files, network or extension installs; settings cannot be changed afterwards.
_CONFIG = {"enable_external_access": False, "lock_configuration": True}


class DuckDBWarehouse:
    dialect: Dialect = Dialect.DUCKDB

    def __init__(
        self,
        path: str | Path,
        max_rows: int = MAX_ROWS,
        statement_timeout_s: float | None = DEFAULT_STATEMENT_TIMEOUT_S,
    ) -> None:
        db_path = Path(path)
        if not db_path.exists():
            raise WarehouseError(f"DuckDB file not found: {db_path.name}")
        self._max_rows = max_rows
        self._timeout_s = statement_timeout_s
        self._host_paths = _host_replacements(db_path)
        try:
            self._con = duckdb.connect(str(db_path), read_only=True, config=dict(_CONFIG))
        except duckdb.Error as exc:
            raise WarehouseError(f"Could not open DuckDB database: {type(exc).__name__}") from None

    @classmethod
    def from_profile(cls, profile: WarehouseProfile) -> DuckDBWarehouse:
        path = profile.options.get("path")
        if not isinstance(path, str) or not path:
            raise WarehouseError(f"Profile '{profile.name}' needs options.path (DuckDB file)")
        return cls(path, max_rows=profile.max_rows, statement_timeout_s=profile.statement_timeout_s)

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
            raise self._error("Schema lookup failed", exc) from None
        return {col: dtype for col, dtype in rows}

    def explain(self, sql: str) -> None:
        def action() -> None:
            self._require_single_select(sql)
            self._con.execute("EXPLAIN " + sql).fetchall()

        self._guarded("Query does not compile", action)

    def run(self, sql: str, limit: int) -> QueryResult:
        limit = effective_limit(limit, self._max_rows)

        def action() -> QueryResult:
            self._require_single_select(sql)
            return limited_result(self._con.execute(sql), limit)

        return self._guarded("Query failed", action)

    def _error(self, context: str, exc: BaseException) -> WarehouseError:
        return WarehouseError(_scrub_host(f"{context}: {exc}", self._host_paths))

    def _guarded(self, context: str, action: Callable[[], T]) -> T:
        """Run `action` under the statement timeout; driver errors become scrubbed
        WarehouseErrors, an interrupted call `query timed out after Ns`."""
        if self._timeout_s is None:
            try:
                return action()
            except duckdb.Error as exc:
                raise self._error(context, exc) from None
        lock = threading.Lock()
        state = {"done": False, "fired": False}

        def interrupt() -> None:
            with lock:
                if not state["done"]:
                    state["fired"] = True
                    self._con.interrupt()

        timer = threading.Timer(self._timeout_s, interrupt)
        timer.daemon = True
        timer.start()
        try:
            return action()
        except duckdb.Error as exc:
            if state["fired"]:
                raise timeout_error(self._timeout_s) from None
            raise self._error(context, exc) from None
        finally:
            with lock:
                state["done"] = True
            timer.cancel()

    def _require_single_select(self, sql: str) -> None:
        """Refuse anything but exactly one SELECT, as DuckDB itself parses the string."""
        statements = self._con.extract_statements(sql)
        if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
            kinds = ", ".join(s.type.name for s in statements) or "none"
            raise WarehouseError(f"Only a single SELECT statement is allowed (got: {kinds})")


def _host_replacements(db_path: Path) -> list[tuple[str, str]]:
    """(text, replacement) pairs, longest first, for paths and names of this host."""
    pairs: dict[str, str] = {}
    for variant in {db_path, db_path.absolute(), db_path.resolve()}:
        pairs[str(variant)] = variant.name
        pairs[str(variant.parent)] = "<warehouse dir>"
    try:
        home = Path.home()
        pairs[str(home)] = "~"
        pairs[str(home.resolve())] = "~"
    except (RuntimeError, OSError):
        pass
    pairs.pop(".", None)
    pairs.pop(os.sep, None)
    return sorted(((k, v) for k, v in pairs.items() if k), key=lambda kv: -len(kv[0]))


def _os_user() -> str | None:
    try:
        name = getpass.getuser()
    except Exception:  # noqa: BLE001 - no user name known: nothing to scrub
        return None
    return name if len(name) >= _MIN_USER_LENGTH else None


def _scrub_host(message: str, replacements: list[tuple[str, str]]) -> str:
    """Remove host paths (database file and directory, home directory) and the OS user
    name from a driver message (CLAUDE.md: errors stripped of connection details)."""
    for text, replacement in replacements:
        message = message.replace(text, replacement)
    user = _os_user()
    if user:
        message = re.sub(rf"(?<![\w-]){re.escape(user)}(?![\w-])", REDACTED, message)
    return message
