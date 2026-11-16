"""Use-cases: review proposals (DESIGN §9, §18; ADR 0007, 0018).

- list/get: any viewer; each proposal comes with a unified diff against its base and the
  impact preview (ids of recipes using the target definition).
- approve: approver/admin, or the target definition's owner (trust.approval.can_approve).
  Changes to a VERIFIED target (definition, recipe or join) need a human. A proposal whose
  checks hold an error cannot be approved (PolicyError): revise it instead. The base version
  is re-checked: a target that moved on raises StaleVersionError (stale_base_version issue
  on `.issues`) and the proposal stays pending.
- approve(verify=True) / verify_definition: human approvers/admins only (can_verify; owners
  may not verify). Only what passed complete checks can be verified: a proposal with a
  `checks_incomplete` issue, a proposal whose definition the checks left DRAFT (e.g. a
  `type_unknown` warning, or a metadata-only revision of a draft) and verify_definition on
  a DRAFT version are refused (PolicyError).
- Decisions are serialized (`_apply.DECISIONS`): the proposal is re-read under the lock, so
  of a concurrent approve and reject only one succeeds; the other raises PolicyError.
- reject: same permission as approve; a comment is required. withdraw: the author only,
  who must still hold the contributor role (Action.PROPOSE).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from cteforge.domain.audit import AuditKind
from cteforge.domain.errors import NotFoundError, PermissionDeniedError, PolicyError
from cteforge.domain.issues import IssueCode, has_errors
from cteforge.domain.models import Definition, DefinitionState
from cteforge.domain.proposals import Proposal, ProposalKind, ProposalStatus
from cteforge.domain.users import ActorKind, User
from cteforge.services import _apply, _audit
from cteforge.services.auth import require
from cteforge.services.cards import (
    DefinitionCard,
    ProposalOutcome,
    ProposalView,
    card_from_definition,
)
from cteforge.services.context import AppContext
from cteforge.trust.approval import Action

_DEFINITION_KINDS = (ProposalKind.NEW_DEFINITION, ProposalKind.REVISE_DEFINITION)


class _Target(BaseModel):
    model_config = ConfigDict(frozen=True)

    definition: Definition | None = None  # Current latest definition (definition kinds).
    state: DefinitionState | None = None  # Current state of whatever the proposal changes.


def _get(ctx: AppContext, id: str) -> Proposal:
    proposal = ctx.store.get_proposal(id)
    if proposal is None:
        raise NotFoundError(f"Proposal '{id}' does not exist")
    return proposal


def _pending(proposal: Proposal) -> None:
    if proposal.status is not ProposalStatus.PENDING:
        raise PolicyError(
            f"Proposal '{proposal.id}' is {proposal.status.value}; only pending proposals "
            "can be decided"
        )


def _target(ctx: AppContext, proposal: Proposal) -> _Target:
    if proposal.kind in _DEFINITION_KINDS:
        definition = ctx.store.get_definition(proposal.target_id)
        return _Target(definition=definition, state=definition.state if definition else None)
    return _Target(state=_apply.target_state(ctx, proposal))


def _require_decide(user: User, proposal: Proposal, target: _Target) -> None:
    """Approve/reject permission: can_approve on the target definition; for recipes and
    joins the approver role, and a human when the target is verified."""
    if proposal.kind in _DEFINITION_KINDS:
        require(user, Action.APPROVE, target.definition)
        return
    require(user, Action.APPROVE)
    if target.state is DefinitionState.VERIFIED and user.kind is ActorKind.AGENT:
        raise PermissionDeniedError(
            f"'{proposal.target_id}' is verified; only a person may approve changes to it"
        )


def _audit_fields(id: str, op: str) -> dict[str, Any]:
    return {"extra": {"op": op, "proposal_id": id}}


def list_proposals(
    ctx: AppContext, user: User, status: ProposalStatus | None = None
) -> list[ProposalView]:
    """Proposals, newest first, each with diff and impact preview."""
    with _audit.audit_failures(ctx, user, AuditKind.REVIEW, extra={"op": "list_proposals"}):
        require(user, Action.DESCRIBE)
        views = [_apply.view(ctx, p) for p in ctx.store.list_proposals(status)]
    _audit.record(
        ctx,
        user,
        AuditKind.REVIEW,
        fail_closed=False,
        extra={
            "op": "list_proposals",
            "status": status.value if status else None,
            "count": len(views),
        },
    )
    return views


def get_proposal(ctx: AppContext, user: User, id: str) -> ProposalView:
    with _audit.audit_failures(ctx, user, AuditKind.REVIEW, **_audit_fields(id, "get_proposal")):
        require(user, Action.DESCRIBE)
        result = _apply.view(ctx, _get(ctx, id))
    _audit.record(
        ctx, user, AuditKind.REVIEW, fail_closed=False, **_audit_fields(id, "get_proposal")
    )
    return result


def approve(
    ctx: AppContext, user: User, id: str, *, verify: bool = False, comment: str = ""
) -> ProposalOutcome:
    """Apply a pending proposal; with verify=True the result is VERIFIED by `user`."""
    with _audit.audit_failures(ctx, user, AuditKind.REVIEW, **_audit_fields(id, "approve")):
        proposal = _get(ctx, id)
        target = _target(ctx, proposal)
        _require_decide(user, proposal, target)
        if verify:
            require(user, Action.VERIFY, target.definition)
        _pending(proposal)
        if has_errors(proposal.checks):
            raise PolicyError(
                f"Proposal '{id}' has failing checks; it cannot be approved. Reject it or "
                "ask for a revision."
            )
        if verify and any(i.code is IssueCode.CHECKS_INCOMPLETE for i in proposal.checks):
            raise PolicyError(
                f"Proposal '{id}' was not fully checked (checks_incomplete); it can be "
                "approved but not verified."
            )
        if verify and _apply.proposed_state(proposal) is DefinitionState.DRAFT:
            raise PolicyError(
                f"Proposal '{id}' is a draft (its checks did not all pass); it can be "
                "approved as a draft but not verified. Revise it so the checks pass."
            )
        decided, applied = _apply.apply_proposal(
            ctx, proposal, user, auto=False, verify=verify, comment=comment
        )
    _apply.record_proposal(ctx, user, AuditKind.REVIEW, decided, "approve")
    return _apply.outcome(ctx, decided, applied)


def reject(ctx: AppContext, user: User, id: str, comment: str) -> ProposalView:
    """Reject a pending proposal; the comment says why (kept on the proposal)."""
    with _audit.audit_failures(ctx, user, AuditKind.REVIEW, **_audit_fields(id, "reject")):
        proposal = _get(ctx, id)
        _require_decide(user, proposal, _target(ctx, proposal))
        _pending(proposal)
        if not comment.strip():
            raise PolicyError("A rejection needs a comment saying why")
        decided = _apply.decide(ctx, proposal, user, ProposalStatus.REJECTED, comment=comment)
    _apply.record_proposal(ctx, user, AuditKind.REVIEW, decided, "reject")
    return _apply.view(ctx, decided)


def withdraw(ctx: AppContext, user: User, id: str) -> ProposalView:
    """The author takes back their own pending proposal."""
    with _audit.audit_failures(ctx, user, AuditKind.REVIEW, **_audit_fields(id, "withdraw")):
        require(user, Action.PROPOSE)
        proposal = _get(ctx, id)
        if proposal.author != user.name:
            raise PermissionDeniedError("Only the author may withdraw a proposal")
        _pending(proposal)
        decided = _apply.decide(ctx, proposal, user, ProposalStatus.WITHDRAWN)
    _apply.record_proposal(ctx, user, AuditKind.REVIEW, decided, "withdraw")
    return _apply.view(ctx, decided)


def verify_definition(ctx: AppContext, user: User, id: str, version: int) -> DefinitionCard:
    """Mark the LATEST version of a definition verified by `user` today (human approver)."""
    fields = {"definitions": [f"{id}@{version}"], "extra": {"op": "verify_definition"}}
    with _audit.audit_failures(ctx, user, AuditKind.REVIEW, **fields):
        current = ctx.store.get_definition(id)
        require(user, Action.VERIFY, current)
        if current is None:
            raise NotFoundError(f"Definition '{id}' does not exist")
        if current.version != version:
            raise _apply.stale_error("Definition", id, current.version, version)
        if current.state is DefinitionState.DRAFT:
            raise PolicyError(
                f"Definition '{id}' v{version} is a draft: its checks failed or did not run. "
                "Revise it so the checks pass before verifying."
            )
        verified = ctx.store.set_definition_state(
            id,
            version,
            DefinitionState.VERIFIED,
            verified_by=user.name,
            verified_at=ctx.now().date(),
        )
    _audit.record(ctx, user, AuditKind.REVIEW, **fields)
    return card_from_definition(verified)
