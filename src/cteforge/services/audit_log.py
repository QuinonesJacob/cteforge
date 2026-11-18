"""Use-case: list audit events (DESIGN §11, ADR 0008).

Who may read it: users holding the view_audit tool (built-in approver and admin; ADR
0027). The log holds every actor's questions and variable values, so viewers and
contributors — agents included — cannot browse it. Events come newest first; `limit` is
clamped to 1..1000 and `has_more` says whether older matching events exist.

The listing itself is recorded as an `admin` event with `extra.op = "list_audit"`
(best-effort, `fail_closed=False`, after the read so a page never contains its own
listing).
"""

from __future__ import annotations

import datetime as dt

from cteforge.domain.access import Tool
from cteforge.domain.audit import AuditKind
from cteforge.domain.errors import PolicyError
from cteforge.domain.users import User
from cteforge.services import _audit
from cteforge.services.auth import require
from cteforge.services.cards import AuditPage
from cteforge.services.context import AppContext

MAX_LIMIT = 1000


def list_audit(
    ctx: AppContext,
    user: User,
    *,
    limit: int = 100,
    actor: str | None = None,
    kind: AuditKind | None = None,
    definition_id: str | None = None,
    since: dt.datetime | None = None,
) -> AuditPage:
    """Matching events, newest first. Filters combine (AND); `definition_id` matches any
    event that used any version of it; `since` must be timezone-aware. An unknown `kind`
    or a naive `since` raises PolicyError (checked after the permission, and audited)."""
    with _audit.audit_failures(ctx, user, AuditKind.ADMIN, extra={"op": "list_audit"}):
        require(user, Tool.VIEW_AUDIT)
        try:
            parsed_kind = AuditKind(kind) if kind is not None else None
        except ValueError:
            raise PolicyError(f"kind must be one of {[k.value for k in AuditKind]}") from None
        if since is not None and since.utcoffset() is None:
            raise PolicyError("since must be timezone-aware (for example 2026-01-01T00:00Z)")
    filters = {
        "actor": actor,
        "kind": parsed_kind.value if parsed_kind is not None else None,
        "definition_id": definition_id,
        "since": since.isoformat() if since is not None else None,
    }
    limit = max(1, min(limit, MAX_LIMIT))
    events = ctx.audit.list_events(
        limit=limit + 1,
        actor=actor,
        kind=parsed_kind,
        definition_id=definition_id,
        since=since,
    )
    page = AuditPage(events=events[:limit], limit=limit, has_more=len(events) > limit)
    _audit.record(
        ctx,
        user,
        AuditKind.ADMIN,
        fail_closed=False,
        extra={"op": "list_audit", "filters": filters, "returned": len(page.events)},
    )
    return page
