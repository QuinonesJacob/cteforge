"""Proposals and review (DESIGN §18; ADR 0007, 0018)."""

from __future__ import annotations

from fastapi import APIRouter

from cteforge.api.auth import Ctx, CurrentUser
from cteforge.api.schemas import (
    ApproveRequest,
    ProposeDefinitionRequest,
    ProposeJoinRequest,
    RejectRequest,
    RenameDefinitionRequest,
    ReviseDefinitionRequest,
)
from cteforge.domain.proposals import ProposalStatus
from cteforge.services import propose, propose_other, review
from cteforge.services.cards import ProposalOutcome, ProposalView

router = APIRouter(tags=["proposals"])


@router.post("/proposals/definitions", response_model=ProposalOutcome)
def propose_definition(
    ctx: Ctx, user: CurrentUser, body: ProposeDefinitionRequest
) -> ProposalOutcome:
    return propose.propose_definition(
        ctx,
        user,
        sql=body.sql,
        composition=body.composition,
        dialect=body.dialect,
        metadata=body.metadata,
        annotations=body.annotations,
        profile=body.profile,
        summary=body.summary,
    )


@router.post("/proposals/definitions/{id}/revise", response_model=ProposalOutcome)
def revise_definition(
    ctx: Ctx, user: CurrentUser, id: str, body: ReviseDefinitionRequest
) -> ProposalOutcome:
    return propose.revise_definition(
        ctx,
        user,
        id=id,
        base_version=body.base_version,
        sql=body.sql,
        composition=body.composition,
        dialect=body.dialect,
        metadata=body.metadata,
        annotations=body.annotations,
        summary=body.summary,
    )


@router.post("/proposals/definitions/{id}/rename", response_model=ProposalOutcome)
def rename_definition(
    ctx: Ctx, user: CurrentUser, id: str, body: RenameDefinitionRequest
) -> ProposalOutcome:
    return propose.rename_definition(
        ctx,
        user,
        id=id,
        new_id=body.new_id,
        base_version=body.base_version,
        summary=body.summary,
    )


@router.post("/proposals/joins", response_model=ProposalOutcome)
def propose_join(ctx: Ctx, user: CurrentUser, body: ProposeJoinRequest) -> ProposalOutcome:
    return propose_other.propose_join(
        ctx,
        user,
        action=body.action,
        id=body.id,
        left_table=body.left_table,
        right_table=body.right_table,
        keys=body.keys,
        join_type=body.join_type,
        relationship=body.relationship,
        traps=body.traps,
        notes=body.notes,
        summary=body.summary,
    )


@router.get("/proposals", response_model=list[ProposalView])
def list_proposals(
    ctx: Ctx, user: CurrentUser, status: ProposalStatus | None = None
) -> list[ProposalView]:
    return review.list_proposals(ctx, user, status)


# Before /proposals/{id} so "count" is not taken for an id. Not audited (nav badge poll).
@router.get("/proposals/count")
def count_proposals(
    ctx: Ctx, user: CurrentUser, status: ProposalStatus | None = None
) -> dict[str, int]:
    return {"count": review.count_proposals(ctx, user, status)}


@router.get("/proposals/{id}", response_model=ProposalView)
def get_proposal(ctx: Ctx, user: CurrentUser, id: str) -> ProposalView:
    return review.get_proposal(ctx, user, id)


@router.post("/proposals/{id}/approve", response_model=ProposalOutcome)
def approve(ctx: Ctx, user: CurrentUser, id: str, body: ApproveRequest) -> ProposalOutcome:
    return review.approve(ctx, user, id, verify=body.verify, comment=body.comment)


@router.post("/proposals/{id}/reject", response_model=ProposalView)
def reject(ctx: Ctx, user: CurrentUser, id: str, body: RejectRequest) -> ProposalView:
    return review.reject(ctx, user, id, body.comment)


@router.post("/proposals/{id}/withdraw", response_model=ProposalView)
def withdraw(ctx: Ctx, user: CurrentUser, id: str) -> ProposalView:
    return review.withdraw(ctx, user, id)
