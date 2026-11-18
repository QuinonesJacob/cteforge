"""Use-cases: review proposals (DESIGN §9, §18; ADR 0007, 0018).

- list/get: any viewer; each proposal comes with a unified diff against its base and the
  impact preview (ids of recipes using the target definition).
- approve: approver/admin, or the target definition's owner (trust.approval.can_approve).
  Changes to a VERIFIED target (definition, recipe or join) need a human. A proposal whose
  checks hold an error cannot be approved (PolicyError): revise it instead. The base version
  is re-checked: a target that moved on raises StaleVersionError (stale_base_version issue
  on `.issues`) and the proposal stays pending.
- Four-eyes rule (ADR 0018 addendum): nobody but a human admin may approve their own proposal
  (`can_approve(..., author=proposal.author)`) or verify a definition version they
  authored any part of (`can_verify(..., author=...)` for each of `_version_authors`:
  the author of the newest logic-changing version up to it and of every metadata-only
  version since, from the store's history; on approve(verify=True) the proposal's
  author, plus the base version's authors when the revision is metadata-only).
- approve(verify=True) / verify_definition: human approvers/admins only (can_verify; owners
  may not verify). Only what passed complete checks can be verified
  (`_apply.check_verifiable`): a proposal with an error, a `checks_incomplete` or
  `draft_not_usable` issue, a proposal whose definition the checks left DRAFT (e.g. a
  `type_unknown` warning, or a metadata-only revision of a draft) and verify_definition on
  a DRAFT version are refused (PolicyError).
- Decisions are compare-and-swaps on the stored status (`_apply.decide` /
  `apply_proposal`; also serialized in-process by `_apply.DECISIONS`), so of a concurrent
  approve and reject, in one process or several, only one succeeds; the other raises
  PolicyError.
- reject: same permission as approve; a comment is required. withdraw: the author only,
  who must still hold the contributor role (Action.PROPOSE).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict

from cteforge.domain.audit import AuditKind
from cteforge.domain.errors import NotFoundError, PermissionDeniedError, PolicyError
from cteforge.domain.issues import has_errors
from cteforge.domain.models import Definition, DefinitionState, DefinitionVersionInfo
from cteforge.domain.proposals import DEFINITION_KINDS, Proposal, ProposalKind, ProposalStatus
from cteforge.domain.users import ActorKind, User
from cteforge.services import _apply, _audit, _library
from cteforge.services.auth import require
from cteforge.services.cards import (
    DefinitionCard,
    ProposalOutcome,
    ProposalView,
    card_from_definition,
)
from cteforge.services.context import AppContext
from cteforge.trust.approval import Action, can_approve, can_verify

# Kinds whose target is a definition (renames included: approving one needs what
# approving any change to that definition needs; ADR 0024).
_DEFINITION_KINDS = DEFINITION_KINDS


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


def _require_not_own_proposal(user: User, proposal: Proposal, target: _Target) -> None:
    """Four-eyes rule (ADR 0018 addendum): only a human admin may approve their own
    proposal; an agent admin never may."""
    if not can_approve(user, target.definition, author=proposal.author):
        raise PermissionDeniedError(
            f"User '{user.name}' authored proposal '{proposal.id}' and may not approve it "
            "(four-eyes rule); ask another approver"
        )


def _require_not_own_version(user: User, authors: Iterable[str | None], what: str) -> None:
    """Four-eyes rule (ADR 0018 addendum): only a human admin may verify a version they wrote
    any part of (see `_version_authors`)."""
    for author in authors:
        if not can_verify(user, None, author=author):
            raise PermissionDeniedError(
                f"User '{user.name}' authored {what} and may not verify it (four-eyes rule); "
                "ask another approver"
            )


def _version_authors(history: Sequence[DefinitionVersionInfo], version: int) -> set[str]:
    """Everyone who wrote what version `version` contains: the author of the newest version
    up to it that changed the logic (its SQL, variables, grain, outputs, join keys), and
    of every metadata-only version since. A metadata-only revision keeps the earlier
    logic, so its own author is not the only one whose work is being verified: otherwise
    the logic's author could verify it after anyone made a cosmetic edit."""
    authors: set[str] = set()
    for info in sorted(history, key=lambda h: h.version, reverse=True):
        if info.version > version:
            continue
        authors.add(info.author)
        if info.logic_changed:
            break
    return authors


def _proposal_authors(ctx: AppContext, proposal: Proposal) -> set[str]:
    """Authors of the version approving `proposal` would create (`_version_authors`): its
    own author, plus, for a metadata-only revision, the authors of the base version."""
    authors = {proposal.author}
    metadata_only = proposal.kind is ProposalKind.REVISE_DEFINITION and not bool(
        proposal.payload.get("logic_changed", True)
    )
    if metadata_only and proposal.base_version is not None:
        history = ctx.store.definition_history(proposal.target_id)
        authors |= _version_authors(history, proposal.base_version)
    return authors


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


def count_proposals(ctx: AppContext, user: User, status: ProposalStatus | None = None) -> int:
    """How many proposals have `status` (all when None), for the GUI's nav badge. Needs
    Action.SEARCH (any viewer). Deliberately NOT audited: the GUI polls it, and it reveals
    no proposal content."""
    require(user, Action.SEARCH)
    return len(ctx.store.list_proposals(status))


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
        _require_not_own_proposal(user, proposal, target)
        if verify:
            require(user, Action.VERIFY, target.definition)
            _require_not_own_version(
                user, _proposal_authors(ctx, proposal), f"(part of) proposal '{id}'"
            )
        _pending(proposal)
        if has_errors(proposal.checks):
            raise PolicyError(
                f"Proposal '{id}' has failing checks; it cannot be approved. Reject it or "
                "ask for a revision."
            )
        if verify:
            _apply.check_verifiable(proposal)
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
            raise _library.definition_not_found(ctx, id)
        if current.version != version:
            raise _apply.stale_error("Definition", id, current.version, version)
        history = ctx.store.definition_history(id)
        _require_not_own_version(
            user, _version_authors(history, version), f"(part of) '{id}' v{version}"
        )
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
