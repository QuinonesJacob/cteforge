"""cteforge's own audit log in its own ``audit_events`` table (DESIGN §11, ADR 0008/0016).

Stores metadata about cteforge calls only: never result rows, SQL text, credentials or tokens
(``sql_sha256`` is a hash). JSON fields are stored as Text so SQLite and Postgres behave alike.
Timestamps are stored in UTC; naive datetimes are rejected.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Iterator
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Engine, Row
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from sqlalchemy.pool import StaticPool

from cteforge.domain.audit import AuditEvent, AuditKind
from cteforge.domain.errors import AuditError

_HEX = re.compile(r"[0-9a-f]+")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_JSON_FIELDS = ("definitions", "values", "extra")

_metadata = sa.MetaData()

audit_events = sa.Table(
    "audit_events",
    _metadata,
    sa.Column("seq", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("id", sa.String(64), nullable=False, unique=True),
    sa.Column("at", sa.DateTime(timezone=True), nullable=False, index=True),
    sa.Column("actor", sa.String(200), nullable=False, index=True),
    sa.Column("actor_kind", sa.String(20), nullable=False),
    sa.Column("kind", sa.String(40), nullable=False, index=True),
    sa.Column("question", sa.Text, nullable=True),
    sa.Column("definitions", sa.Text, nullable=False),
    sa.Column("values", sa.Text, nullable=False),
    sa.Column("trust_label", sa.String(40), nullable=True),
    sa.Column("compile_id", sa.String(64), nullable=True),
    sa.Column("sql_sha256", sa.String(64), nullable=True),
    sa.Column("row_count", sa.Integer, nullable=True),
    sa.Column("duration_ms", sa.Integer, nullable=True),
    sa.Column("outcome", sa.String(20), nullable=False),
    sa.Column("error", sa.Text, nullable=True),
    sa.Column("extra", sa.Text, nullable=False),
)


def _exc_name(exc: Exception) -> str:
    """Exception class name only: SQLAlchemy messages embed statements and parameters."""
    return type(exc).__name__


def _make_engine(url: str) -> Engine:
    try:
        parsed = sa.make_url(url)
    except ArgumentError as exc:
        raise AuditError(f"invalid audit database URL ({_exc_name(exc)})") from exc
    if parsed.get_backend_name() == "sqlite" and parsed.database in (None, "", ":memory:"):
        return sa.create_engine(
            parsed, poolclass=StaticPool, connect_args={"check_same_thread": False}
        )
    return sa.create_engine(parsed)


def _to_utc(value: dt.datetime, what: str) -> dt.datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise AuditError(f"{what} must be timezone-aware")
    return value.astimezone(dt.UTC)


def _check_event(event: AuditEvent) -> None:
    if event.compile_id is not None and not _HEX.fullmatch(event.compile_id):
        raise AuditError("compile_id must be lowercase hex")
    if event.sql_sha256 is not None and not _SHA256.fullmatch(event.sql_sha256):
        raise AuditError("sql_sha256 must be a 64-char lowercase hex digest, not SQL text")


def _event_to_row(event: AuditEvent) -> dict[str, Any]:
    _check_event(event)
    row = event.model_dump(mode="json")
    row["at"] = _to_utc(event.at, "event.at")
    for name in _JSON_FIELDS:
        row[name] = json.dumps(row[name], sort_keys=True)
    return row


def _row_to_event(row: Row[Any]) -> AuditEvent:
    data = dict(row._mapping)
    data.pop("seq")
    at: dt.datetime = data["at"]
    data["at"] = at.replace(tzinfo=dt.UTC) if at.tzinfo is None else at.astimezone(dt.UTC)
    for name in _JSON_FIELDS:
        data[name] = json.loads(data[name])
    return AuditEvent.model_validate(data)


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _definitions_prefilter(definition_id: str) -> sa.ColumnElement[bool]:
    """Superset match in SQL: a JSON string starting with ``<id>@``. Verified in Python."""
    encoded = json.dumps(definition_id + "@")[1:-1]  # JSON-escaped, without quotes
    pattern = '%"' + _like_escape(encoded) + "%"
    return audit_events.c.definitions.like(pattern, escape="\\")


def _uses_definition(event: AuditEvent, definition_id: str) -> bool:
    prefix = definition_id + "@"
    return any(entry.startswith(prefix) for entry in event.definitions)


class AuditLog:
    """Append-only log of cteforge's own calls, in the store's database."""

    def __init__(self, url: str) -> None:
        self._engine = _make_engine(url)

    def close(self) -> None:
        """Dispose the engine's connections. In-memory SQLite data is gone afterwards."""
        self._engine.dispose()

    def init_schema(self) -> None:
        """Create the ``audit_events`` table and indexes if missing. Idempotent."""
        try:
            _metadata.create_all(self._engine)
        except SQLAlchemyError as exc:
            raise AuditError(f"could not create audit schema ({_exc_name(exc)})") from exc

    def record(self, event: AuditEvent) -> None:
        """Append one event. Raises ``AuditError`` on invalid data or database failure."""
        row = _event_to_row(event)
        try:
            with self._engine.begin() as conn:
                conn.execute(audit_events.insert().values(**row))
        except SQLAlchemyError as exc:
            raise AuditError(f"could not record audit event {event.id} ({_exc_name(exc)})") from exc

    def list_events(
        self,
        *,
        limit: int = 100,
        actor: str | None = None,
        kind: AuditKind | None = None,
        definition_id: str | None = None,
        since: dt.datetime | None = None,
    ) -> list[AuditEvent]:
        """Events matching every given filter, newest first, at most ``limit``."""
        if limit < 1:
            return []
        query = self._query(actor=actor, kind=kind, definition_id=definition_id, since=since)
        if definition_id is None:
            query = query.limit(limit)
        try:
            return list(self._collect(query, definition_id, limit))
        except SQLAlchemyError as exc:
            raise AuditError(f"could not read audit events ({_exc_name(exc)})") from exc

    def _query(
        self,
        *,
        actor: str | None,
        kind: AuditKind | None,
        definition_id: str | None,
        since: dt.datetime | None,
    ) -> sa.Select[Any]:
        query = sa.select(audit_events)
        if actor is not None:
            query = query.where(audit_events.c.actor == actor)
        if kind is not None:
            query = query.where(audit_events.c.kind == AuditKind(kind).value)
        if since is not None:
            query = query.where(audit_events.c.at >= _to_utc(since, "since"))
        if definition_id is not None:
            query = query.where(_definitions_prefilter(definition_id))
        return query.order_by(audit_events.c.at.desc(), audit_events.c.seq.desc())

    def _collect(
        self, query: sa.Select[Any], definition_id: str | None, limit: int
    ) -> Iterator[AuditEvent]:
        found = 0
        with self._engine.connect() as conn:
            for row in conn.execute(query):
                event = _row_to_event(row)
                if definition_id is not None and not _uses_definition(event, definition_id):
                    continue
                yield event
                found += 1
                if found >= limit:
                    return
