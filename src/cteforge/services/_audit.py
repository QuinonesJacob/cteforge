"""Record an AuditEvent for a use-case call (DESIGN §11, ADR 0008).

Failure policy: **fail closed by default.** If the event cannot be written, `record` raises
AuditError and the use-case must fail too — a run whose audit entry is lost must not hand
back rows (record the run's event before returning its outcome). Read-only lookups (search,
get_definition, parse_query, audit listing) may pass `fail_closed=False`: the failure is
logged (event id and exception class only) and the call proceeds.

Outcomes (one rule for every use-case): OK on success; REJECTED when a call is refused by
policy — PermissionDeniedError, PolicyError, StaleVersionError, or a refusal returned as an
outcome (trust, strict mode, not read-only); ERROR for every other failure.
`audit_failures` applies that rule to a raised CteforgeError (best effort, then re-raise).

Never stored: SQL text (pass `sql=` and only its SHA-256 is kept), result rows,
credentials, tokens. Error text is the exception class name only, since warehouse and
parser messages can quote SQL. Error messages raised from here never include field values.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from pydantic import ValidationError

from cteforge.domain.audit import AuditEvent, AuditKind, AuditOutcome
from cteforge.domain.errors import (
    AuditError,
    CteforgeError,
    PermissionDeniedError,
    PolicyError,
    StaleVersionError,
)
from cteforge.domain.users import User
from cteforge.services.cards import json_safe
from cteforge.services.context import AppContext

_log = logging.getLogger(__name__)
_FORBIDDEN_FIELDS = {"id", "at", "actor", "actor_kind", "kind", "sql_sha256", "error"}


def sql_sha256(sql: str) -> str:
    """Hex SHA-256 of the exact SQL executed (tag included), for joining to warehouse logs
    without storing the SQL."""
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def error_summary(error: BaseException | str | None) -> str | None:
    """What the log keeps about a failure: the exception class name; strings are kept as
    given (callers pass only fixed, value-free text)."""
    if error is None or isinstance(error, str):
        return error
    return type(error).__name__


def build_event(
    ctx: AppContext,
    user: User,
    kind: AuditKind,
    *,
    sql: str | None = None,
    error: BaseException | str | None = None,
    outcome: AuditOutcome | None = None,
    **fields: Any,
) -> AuditEvent:
    """An AuditEvent for `user` doing `kind` now. `fields` are the remaining AuditEvent
    fields (question, definitions, values, trust_label, compile_id, row_count,
    duration_ms, extra). An `error` with no explicit outcome marks the event ERROR."""
    reserved = sorted(_FORBIDDEN_FIELDS & set(fields))
    if reserved:
        raise AuditError(f"audit fields {reserved} are set by the helper, not the caller")
    for name in ("values", "extra"):
        if name in fields and fields[name] is not None:
            fields[name] = json_safe(fields[name])
    if outcome is None:
        outcome = AuditOutcome.ERROR if error is not None else AuditOutcome.OK
    try:
        return AuditEvent(
            id=uuid.uuid4().hex,
            at=ctx.now(),
            actor=user.name,
            actor_kind=user.kind,
            kind=kind,
            sql_sha256=sql_sha256(sql) if sql is not None else None,
            error=error_summary(error),
            outcome=outcome,
            **fields,
        )
    except ValidationError as exc:
        where = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})
        raise AuditError(f"invalid audit event fields {where}") from None


def record(
    ctx: AppContext,
    user: User,
    kind: AuditKind,
    *,
    fail_closed: bool = True,
    sql: str | None = None,
    error: BaseException | str | None = None,
    outcome: AuditOutcome | None = None,
    **fields: Any,
) -> AuditEvent | None:
    """Build and write one event. Returns it, or None when it could not be written and
    `fail_closed=False`. With `fail_closed=True` (default) raises AuditError instead."""
    event = build_event(ctx, user, kind, sql=sql, error=error, outcome=outcome, **fields)
    try:
        ctx.audit.record(event)
    except Exception as exc:
        failure = (
            exc
            if isinstance(exc, AuditError)
            else AuditError(f"could not record audit event {event.id} ({type(exc).__name__})")
        )
        if fail_closed:
            raise failure from None
        _log.warning("audit event %s not recorded (%s)", event.id, type(exc).__name__)
        return None
    return event


_REFUSALS = (PermissionDeniedError, PolicyError, StaleVersionError)


def failure_outcome(error: BaseException) -> AuditOutcome:
    """REJECTED for a policy refusal (permission, policy, stale base), else ERROR."""
    return AuditOutcome.REJECTED if isinstance(error, _REFUSALS) else AuditOutcome.ERROR


@contextmanager
def audit_failures(ctx: AppContext, user: User, kind: AuditKind, **fields: Any) -> Iterator[None]:
    """Record a CteforgeError raised in the block (outcome per `failure_outcome`, class name
    only) and re-raise it. The write is best effort (`fail_closed=False`) so the caller
    sees the real error; an AuditError from the block itself is re-raised unrecorded."""
    try:
        yield
    except AuditError:
        raise
    except CteforgeError as exc:
        record(
            ctx, user, kind, fail_closed=False, error=exc, outcome=failure_outcome(exc), **fields
        )
        raise
