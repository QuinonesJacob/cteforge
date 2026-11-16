"""Audit log reads (DESIGN §11, ADR 0008): approvers and admins only (services decide)."""

from __future__ import annotations

from fastapi import APIRouter

from cteforge.api.auth import Ctx, CurrentUser
from cteforge.services import audit_log
from cteforge.services.cards import AuditPage

router = APIRouter(tags=["audit"])


@router.get("/audit", response_model=AuditPage)
def list_audit(
    ctx: Ctx,
    user: CurrentUser,
    actor: str | None = None,
    kind: str | None = None,
    definition_id: str | None = None,
    limit: int = 100,
) -> AuditPage:
    # `kind` is validated by the service (unknown kind → PolicyError → 409).
    return audit_log.list_audit(
        ctx,
        user,
        limit=limit,
        actor=actor,
        kind=kind,  # type: ignore[arg-type]
        definition_id=definition_id,
    )
