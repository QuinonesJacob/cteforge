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

What is logged (ADR 0008 addendum, ADR 0029): every call made through MCP (reads included:
that is how people see what agents looked up); every run (compile_and_run, check_and_run)
and every change from any surface; every refused or failed call from any surface. A
SUCCESSFUL READ through the HTTP API (the GUI included) or the CLI is not written. The
surface is set by the adapter (`surface("mcp")` around each MCP tool call, `"cli"` by the
CLI; default `"api"`); which use-cases are reads is decided here, in one place
(`READ_USE_CASES`; every use-case is classified, tests/unit/services/test_support_audit.py).
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Literal

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

# ── What is logged (ADR 0008 addendum, ADR 0029) ───────────────────────────────────────

Surface = Literal["mcp", "api", "cli"]
SURFACE: ContextVar[Surface] = ContextVar("cteforge_audit_surface", default="api")


@contextmanager
def surface(name: Surface) -> Iterator[None]:
    """Mark the calls made inside the block as coming from `name` (set by the adapter)."""
    token = SURFACE.set(name)
    try:
        yield
    finally:
        SURFACE.reset(token)


# Use-cases (services function names; an event's `extra.op`, else its kind) by class.
# Reads are logged only through MCP (or when refused / failed); runs and writes always.
READ_USE_CASES = frozenset(
    {
        "search",
        "list_library",
        "get_definition",
        "definition_history",
        "definition_references",
        "search_document",
        "parse_query",
        "list_joins",
        "describe_tables",
        "list_proposals",
        "get_proposal",
        "list_users",
        "list_roles",
        "list_tokens",
        "get_settings",
        "list_audit",
        "export_library",
    }
)
RUN_USE_CASES = frozenset({"compile_and_run", "check_and_run"})
WRITE_USE_CASES = frozenset(
    {
        "propose_definition",
        "revise_definition",
        "rename_definition",
        "propose_join",
        "import_library",
        "approve",
        "reject",
        "withdraw",
        "verify_definition",
        "revalidate",
        "bootstrap_admin",
        "create_user",
        "update_user",
        "set_role",
        "set_active",
        "update_profile",
        "create_role",
        "update_role",
        "delete_role",
        "create_token",
        "revoke_token",
        "set_token_tools",
        "set_settings",
        "set_default_connection",
        "rebuild_search_index",
    }
)
# Never audited at all: the nav badge poll and the caller's own access (services/CLAUDE.md).
UNAUDITED_USE_CASES = frozenset({"count_proposals", "me"})
_RUN_KINDS = frozenset({AuditKind.COMPILE_AND_RUN, AuditKind.CHECK_AND_RUN})


def is_read(kind: AuditKind, extra: Mapping[str, Any] | None) -> bool:
    """Whether an event is a read-only lookup (`extra.op`, else the kind's name)."""
    op = (extra or {}).get("op") or kind.value
    return kind not in _RUN_KINDS and op in READ_USE_CASES


def should_log(event: AuditEvent) -> bool:
    """False only for a successful read outside MCP (a person reading in the GUI / API /
    CLI); refusals, failures, runs, writes and anything through MCP are logged."""
    if event.outcome is not AuditOutcome.OK or SURFACE.get() == "mcp":
        return True
    return not is_read(event.kind, event.extra)


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
    if user.token_id is not None:
        # The calling token (ADR 0029: the Log shows it): id and display prefix only.
        # A minted / revoked token is the event's `subject_token_id`, never these keys.
        fields["extra"] = {
            **(fields.get("extra") or {}),
            "token_id": user.token_id,
            "token_prefix": user.token_prefix,
        }
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
    `fail_closed=False` — or when it is not logged at all (`should_log`: a successful read
    outside MCP). With `fail_closed=True` (default) raises AuditError instead."""
    event = build_event(ctx, user, kind, sql=sql, error=error, outcome=outcome, **fields)
    if not should_log(event):
        return None
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
