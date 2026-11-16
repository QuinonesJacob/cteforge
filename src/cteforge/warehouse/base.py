"""Warehouse protocol and shared connector helpers (HARDENED module — read CLAUDE.md here
before editing).

The helpers here are the single place that resolves secrets, scrubs driver messages,
imports optional drivers and enforces row limits, so every connector behaves the same.
"""

from __future__ import annotations

import importlib
import os
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import ModuleType
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict

from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import WarehouseError

REDACTED = "***"
# Options that identify where/as whom we connect; scrubbed from driver messages too.
CONNECTION_DETAIL_OPTIONS = frozenset(
    {
        "host",
        "hostaddr",
        "port",
        "user",
        "dbname",
        "database",
        "account",
        "warehouse",
        "role",
        "project",
        "server_hostname",
        "http_path",
        "credentials_file",
    }
)
# Option names that hold a secret (or a connection string that may embed one). These may
# only be given by environment-variable reference in `secrets_env` (ADR 0020).
_SECRET_OPTION = re.compile(
    r"pass(word|wd|code|phrase)|pwd|token|secret|private_key|credentials_json|api_key"
    r"|conninfo|dsn",
    re.IGNORECASE,
)

T = TypeVar("T")


class QueryResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    columns: list[str]
    rows: list[tuple[Any, ...]]
    truncated: bool  # True if more rows existed than the limit.


class Warehouse(Protocol):
    dialect: Dialect

    def column_types(self, table: str) -> dict[str, str]:
        """Column name (lower-case) → warehouse type string. Empty if table unknown."""
        ...

    def explain(self, sql: str) -> None:
        """Raise WarehouseError if the SQL does not compile."""
        ...

    def run(self, sql: str, limit: int) -> QueryResult:
        """Execute read-only SQL, returning at most `limit` rows."""
        ...

    def close(self) -> None:
        """Release the connection. Safe to call more than once."""
        ...


def load_driver(module: str, extra: str) -> ModuleType:
    """Import an optional driver lazily; a missing driver names the install command."""
    try:
        return importlib.import_module(module)
    except ImportError:
        raise WarehouseError(
            f"Driver '{module}' is not installed. Install it with: pip install 'cteforge[{extra}]'"
        ) from None


def reject_plaintext_secrets(profile: WarehouseProfile) -> None:
    """Refuse secret-named options: secrets come only from environment variables."""
    plaintext = sorted(k for k in profile.options if _SECRET_OPTION.search(k))
    if plaintext:
        raise WarehouseError(
            f"Profile '{profile.name}' sets {plaintext} in options; secrets must be "
            "referenced by environment variable in secrets_env"
        )


def resolve_secrets(profile: WarehouseProfile) -> dict[str, str]:
    """Driver parameter → secret value, read from the environment at connect time.

    Refuses plaintext secrets in options first. Errors name the environment variable,
    never a value.
    """
    reject_plaintext_secrets(profile)
    secrets: dict[str, str] = {}
    for param, var in profile.secrets_env.items():
        value = os.environ.get(var)
        if not value:
            raise WarehouseError(
                f"Environment variable {var} (for '{param}' of profile '{profile.name}') is not set"
            )
        secrets[param] = value
    return secrets


_URL = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://\S+")
_KEY_VALUE = re.compile(
    r"\b(password|passwd|pwd|token|access_token|secret|private_key\w*|credentials?"
    r"|host|hostaddr|port|user|dbname|database)"
    r"(\s*[=:]\s*)('[^']*'|\"[^\"]*\"|\S+)",
    re.IGNORECASE,
)
# Quoted names after words that say where/as whom we connect, e.g. `user "ro"`,
# `database "prod"`, `server at "10.1.2.3"`, `resolve host 'db.corp'`.
_QUOTED_DETAIL = re.compile(
    r"\b(user|database|role|host|hostaddr|server at|socket|warehouse|account)"
    r"(\s+)(\"[^\"]*\"|'[^']*')",
    re.IGNORECASE,
)
_PORT = re.compile(r"\b(port)(\s+)(\d+)\b", re.IGNORECASE)
_IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
_IPV6 = re.compile(
    r"(?<![\w:])(?:(?:[0-9a-f]{0,4}:){7}[0-9a-f]{0,4}"
    r"|(?:[0-9a-f]{1,4}(?::[0-9a-f]{1,4})*)?::(?:[0-9a-f]{1,4}(?::[0-9a-f]{1,4})*)?)"
    r"(?![\w:])",
    re.IGNORECASE,
)


def _redact_value(match: re.Match[str]) -> str:
    return f"{match.group(1)}{match.group(2)}{REDACTED}"


def scrub(message: str, secrets: Iterable[str], details: Iterable[str] = ()) -> str:
    """Remove secrets, connection details, URLs, IP addresses, ports and credentials.

    Secret values are removed wherever they occur. Connection details (host, user, ...)
    are removed as whole words, so short values do not garble unrelated words.
    """
    for value in sorted({s for s in secrets if s}, key=len, reverse=True):
        message = message.replace(value, REDACTED)
    message = _URL.sub(REDACTED, message)
    message = _IPV4.sub(REDACTED, message)
    message = _IPV6.sub(REDACTED, message)
    message = _KEY_VALUE.sub(_redact_value, message)
    message = _QUOTED_DETAIL.sub(_redact_value, message)
    message = _PORT.sub(_redact_value, message)
    for value in sorted({d for d in details if d}, key=len, reverse=True):
        message = re.sub(rf"(?<!\w){re.escape(value)}(?!\w)", REDACTED, message)
    return message


@dataclass(frozen=True)
class Sensitive:
    """What a driver message must not reveal: secret values and connection details."""

    secrets: tuple[str, ...] = ()
    details: tuple[str, ...] = ()

    def scrub(self, message: str) -> str:
        return scrub(message, self.secrets, self.details)

    def with_secrets(self, extra: Iterable[str]) -> Sensitive:
        return Sensitive(secrets=(*self.secrets, *extra), details=self.details)


def driver_error(context: str, exc: BaseException, sensitive: Sensitive) -> WarehouseError:
    """A WarehouseError carrying the scrubbed driver message (raise it `from None`)."""
    detail = sensitive.scrub(str(exc).strip()) or type(exc).__name__
    return WarehouseError(f"{context}: {detail}")


def effective_limit(requested: int, max_rows: int) -> int:
    """The row limit actually applied: never above the profile cap, never negative."""
    return max(0, min(requested, max_rows))


def split_table(table: str) -> tuple[str | None, str | None, str] | None:
    """Split `[catalog.][schema.]table` (unquoted names). None if malformed."""
    parts = [p.strip() for p in table.strip().split(".")]
    if not 1 <= len(parts) <= 3 or not all(parts):
        return None
    padded: list[str | None] = [None] * (3 - len(parts)) + list(parts)
    return padded[0], padded[1], parts[-1]


def limited_result(cursor: Any, limit: int) -> QueryResult:
    """Fetch at most `limit` rows from a DB-API cursor; `truncated` if more existed."""
    rows: Sequence[Any] = cursor.fetchmany(limit + 1)
    columns = [d[0] for d in cursor.description or []]
    return QueryResult(
        columns=columns,
        rows=[tuple(r) for r in rows[:limit]],
        truncated=len(rows) > limit,
    )


def connect_kwargs(profile: WarehouseProfile, secrets: dict[str, str]) -> dict[str, Any]:
    """Driver keyword arguments: non-secret options plus resolved secrets."""
    overlap = set(profile.options) & set(secrets)
    if overlap:
        raise WarehouseError(
            f"Profile '{profile.name}' sets {sorted(overlap)} both as option and secret"
        )
    return {**profile.options, **secrets}


def sensitive_values(profile: WarehouseProfile, secrets: dict[str, str]) -> Sensitive:
    """Everything a driver message must not reveal: secrets and connection details."""
    details = tuple(
        str(v)
        for k, v in profile.options.items()
        if k in CONNECTION_DETAIL_OPTIONS and not isinstance(v, bool)
    )
    return Sensitive(secrets=tuple(secrets.values()), details=details)


def columns_query(
    parts: tuple[str | None, str | None, str], placeholder: str, current_schema: str
) -> tuple[str, list[str]]:
    """information_schema.columns lookup with every identifier passed as a parameter.

    Names compare case-insensitively (unquoted semantics). Unqualified names resolve in
    `current_schema` (a constant SQL expression such as ``current_schema()``).
    """
    catalog, schema, name = parts
    sql = (
        "SELECT lower(column_name), data_type FROM information_schema.columns "
        f"WHERE lower(table_name) = lower({placeholder})"
    )
    params = [name]
    if schema is None:
        sql += f" AND table_schema = {current_schema}"
    else:
        sql += f" AND lower(table_schema) = lower({placeholder})"
        params.append(schema)
    if catalog is not None:
        sql += f" AND lower(table_catalog) = lower({placeholder})"
        params.append(catalog)
    return sql + " ORDER BY ordinal_position", params


class DbApiSession:
    """One DB-API connection: cursors are always closed, errors always scrubbed.

    `execute_options` are passed to every `cursor.execute` (e.g. to make the driver refuse
    multi-statement strings). With `rollback_after`, every call ends its transaction with
    a rollback, so per-transaction settings (Postgres READ ONLY) apply to each call anew.
    """

    def __init__(
        self,
        con: Any,
        sensitive: Sensitive,
        *,
        execute_options: Mapping[str, Any] | None = None,
        rollback_after: bool = False,
    ) -> None:
        self._con = con
        self._sensitive = sensitive
        self._options = dict(execute_options or {})
        self._rollback_after = rollback_after

    def call(self, context: str, action: Callable[[Any], T]) -> T:
        """Run `action(cursor)`; any driver exception becomes a scrubbed WarehouseError."""
        try:
            cursor = self._con.cursor()
            try:
                return action(cursor)
            finally:
                cursor.close()
                if self._rollback_after:
                    self._con.rollback()
        except WarehouseError as exc:
            raise WarehouseError(self._sensitive.scrub(str(exc))) from None
        except Exception as exc:
            raise driver_error(context, exc, self._sensitive) from None

    def execute(self, cursor: Any, sql: str, params: Sequence[Any] | None = None) -> None:
        """`cursor.execute` with this session's execute options."""
        cursor.execute(sql, params, **self._options)

    def rows(self, context: str, sql: str, params: Sequence[Any] | None = None) -> list[Any]:
        def action(cursor: Any) -> list[Any]:
            self.execute(cursor, sql, params)
            return list(cursor.fetchall())

        return self.call(context, action)

    def limited(self, sql: str, limit: int) -> QueryResult:
        def action(cursor: Any) -> QueryResult:
            self.execute(cursor, sql)
            return limited_result(cursor, limit)

        return self.call("Query failed", action)

    def close(self) -> None:
        try:
            self._con.close()
        except Exception as exc:
            raise driver_error("Could not close connection", exc, self._sensitive) from None
