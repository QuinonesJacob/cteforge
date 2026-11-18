"""Use-case: the Library — definitions and the proposals touching them, one list (ADR 0026).

`list_library(ctx, user, *, q, trust, approval, created_by, verified_by, needs_my_review,
sort)` → LibraryPage. Needs Tool.SEARCH (it lists the library, like an empty search, but every
state: drafts included, since people review them). Proposal information — pending new
definitions as rows, pending revision/rename badges, each row's approval status, "needs my
review" — is added only for callers who also hold list_proposals; without it rows are the
stored definitions only and the approval filter is ignored. Audit: `search` (op
`list_library`), best effort, like search.

Rows:
- every stored definition (latest version): `approval` is the status of the newest
  proposal touching it (none → approved); `pending` lists its pending proposals (a
  revision or rename shows as a badge linking to its diff);
- every new-definition proposal never applied (its id is not in the library): pending
  ("Pending approval"), rejected or withdrawn; `state` is what its checks earned.
Filters (each a set; empty = no filter on that group):
- `trust`: draft · llm_annotated · verified (the definition's state, or the proposal's);
- `approval`: pending · approved · rejected · withdrawn. A stored definition always
  matches `approved` (its current version was approved) and also its latest proposal's
  status, so a rejected revision never hides it. Default (None): pending + approved —
  rejected and withdrawn rows are hidden;
- `created_by`: human · agent (who proposed it: the author of version 1, or of the
  proposal; the kind comes from the users table);
- `verified_by`: human · agent (who verified it; unverified rows don't match);
- `needs_my_review`: rows with a pending proposal the caller may approve
  (trust.approval.can_approve with the four-eyes rule); needs review_proposal.
- `q`: every word must appear in the id, title, meaning or keywords (case-insensitive).
Order (`sort`, ADR 0029):
- `relevance` (default): with `q`, by score (each word found in the id or title counts 2,
  in the meaning or keywords 1), then trust (verified > llm_annotated > draft > none),
  then title and id; without `q`: trust, then title.
- `oldest`: rows by their oldest pending proposal the caller may decide, then by their
  oldest pending proposal at all; rows with none last (then as `relevance`).
"Waiting on you" (`LibraryPage.waiting`, ADR 0029): every pending proposal the caller may
decide (needs list_proposals and review_proposal), whatever the filters, oldest first.
Each pending brief says whether the caller may decide it (`needs_my_review`); pending
briefs on returned rows and in `waiting` carry their diff's changed-line counts
(`lines_added` / `lines_removed`; computed only for those).
"""

from __future__ import annotations

from collections.abc import Iterable

from cteforge.domain.access import Tool
from cteforge.domain.audit import AuditKind
from cteforge.domain.codec import definition_from_yaml
from cteforge.domain.errors import CteforgeError, PermissionDeniedError, PolicyError
from cteforge.domain.models import Definition, DefinitionState
from cteforge.domain.proposals import DEFINITION_KINDS, Proposal, ProposalKind, ProposalStatus
from cteforge.domain.users import User
from cteforge.services import _apply, _audit
from cteforge.services.auth import permitted, require
from cteforge.services.cards import LibraryPage, LibraryRow, ProposalBrief, WaitingProposal
from cteforge.services.context import AppContext
from cteforge.trust.approval import allowed, can_approve

APPROVALS = ("pending", "approved", "rejected", "withdrawn")
DEFAULT_APPROVAL = frozenset({"pending", "approved"})
SORTS = ("relevance", "oldest")
_TRUST_RANK = {
    DefinitionState.VERIFIED: 0,
    DefinitionState.LLM_ANNOTATED: 1,
    DefinitionState.DRAFT: 2,
}


def _brief(p: Proposal, may_decide: bool = False) -> ProposalBrief:
    return ProposalBrief(
        id=p.id,
        kind=p.kind,
        status=p.status.value,
        author=p.author,
        author_kind=p.author_kind,
        created_at=p.created_at,
        summary=p.summary,
        needs_my_review=may_decide,
    )


def _words(text: str) -> list[str]:
    return "".join(c if c.isalnum() or c == "_" else " " for c in text.lower()).split()


def _matches(q: list[str], row: LibraryRow, keywords: Iterable[str]) -> bool:
    text = " ".join([row.id, row.title, row.meaning, *keywords]).lower()
    return all(w in text for w in q)


def _score(q: list[str], row: LibraryRow, keywords: Iterable[str]) -> int:
    """Relevance: each query word in the id or title counts 2, in the meaning or keywords 1."""
    name = f"{row.id} {row.title}".lower()
    rest = " ".join([row.meaning, *keywords]).lower()
    return sum(2 if w in name else 1 if w in rest else 0 for w in q)


def _trust_rank(row: LibraryRow) -> int:
    return _TRUST_RANK.get(row.state, 3) if row.state is not None else 3


def list_library(
    ctx: AppContext,
    user: User,
    *,
    q: str = "",
    trust: Iterable[str] | None = None,
    approval: Iterable[str] | None = None,
    created_by: Iterable[str] | None = None,
    verified_by: Iterable[str] | None = None,
    needs_my_review: bool = False,
    sort: str = "relevance",
) -> LibraryPage:
    """The Library page (module docstring)."""
    with _audit.audit_failures(ctx, user, AuditKind.SEARCH, extra={"op": "list_library"}):
        require(user, Tool.SEARCH)
        if sort not in SORTS:
            raise PolicyError(f"Unknown sort '{sort}': use one of {', '.join(SORTS)}")
        if needs_my_review and not allowed(user, Tool.REVIEW_PROPOSAL):
            # Membership only: owners review what they own without review_all.
            raise PermissionDeniedError(
                f"User '{user.name}' may not review proposals (review_proposal)"
            )
    with_proposals = permitted(user, Tool.LIST_PROPOSALS)
    kinds = {u.name: u.kind for u in ctx.store.list_users()}
    proposals = ctx.store.list_proposals() if with_proposals else []  # newest first
    by_target: dict[str, list[Proposal]] = {}
    for p in proposals:
        if p.kind in DEFINITION_KINDS:
            by_target.setdefault(p.target_id, []).append(p)

    rows: list[tuple[LibraryRow, list[str]]] = []
    stored: dict[str, Definition] = {d.id: d for d in ctx.store.list_definitions()}
    for d in stored.values():
        touching = by_target.get(d.id, [])
        history = ctx.store.definition_history(d.id)
        creator = history[0].author if history else None
        pending = [p for p in touching if p.status is ProposalStatus.PENDING]
        rows.append(
            (
                LibraryRow(
                    id=d.id,
                    title=d.title,
                    meaning=d.meaning,
                    version=d.version,
                    state=d.state,
                    composed=d.composition is not None,
                    owner=d.owner,
                    created_by=creator,
                    created_by_kind=kinds.get(creator) if creator else None,
                    verified_by=d.verified_by,
                    verified_by_kind=kinds.get(d.verified_by) if d.verified_by else None,
                    approval=touching[0].status.value if touching else "approved",
                    latest_proposal=_brief(touching[0]) if touching else None,
                    pending=[_brief(p, _may_decide(user, p, d)) for p in pending],
                    needs_my_review=any(_may_decide(user, p, d) for p in pending),
                ),
                list(d.keywords),
            )
        )
    # New-definition proposals that never reached the library (newest per id).
    seen: set[str] = set()
    for p in proposals:
        if p.kind is not ProposalKind.NEW_DEFINITION or p.target_id in stored:
            continue
        if p.target_id in seen:
            continue
        seen.add(p.target_id)
        decide = p.status is ProposalStatus.PENDING and _may_decide(user, p, None)
        try:
            proposed = definition_from_yaml(str(p.payload.get("definition_yaml", "")))
        except CteforgeError:
            continue
        rows.append(
            (
                LibraryRow(
                    id=p.target_id,
                    title=proposed.title,
                    meaning=proposed.meaning,
                    state=proposed.state,
                    composed=proposed.composition is not None,
                    owner=proposed.owner,
                    created_by=p.author,
                    created_by_kind=p.author_kind,
                    approval=p.status.value,
                    latest_proposal=_brief(p),
                    pending=[_brief(p, decide)] if p.status is ProposalStatus.PENDING else [],
                    needs_my_review=decide,
                ),
                list(proposed.keywords),
            )
        )

    trusts = set(trust or ())
    approvals = set(approval) if approval is not None else set(DEFAULT_APPROVAL)
    creators = set(created_by or ())
    verifiers = set(verified_by or ())
    words = _words(q)

    def keep(row: LibraryRow, keywords: list[str]) -> bool:
        if trusts and (row.state is None or row.state.value not in trusts):
            return False
        if with_proposals and approvals:
            statuses = {row.approval} | ({"approved"} if row.version is not None else set())
            if not statuses & approvals:
                return False
        if creators and (row.created_by_kind is None or row.created_by_kind.value not in creators):
            return False
        if verifiers and (
            row.verified_by_kind is None or row.verified_by_kind.value not in verifiers
        ):
            return False
        if needs_my_review and not row.needs_my_review:
            return False
        return _matches(words, row, keywords)

    kept = [(row, _score(words, row, kw)) for row, kw in rows if keep(row, kw)]
    kept.sort(key=lambda rs: _order(rs[0], rs[1], sort))
    reviewer = with_proposals and allowed(user, Tool.REVIEW_PROPOSAL)
    waiting = (
        sorted(
            (
                WaitingProposal(**b.model_dump(), target_id=row.id, title=row.title)
                for row, _ in rows
                for b in row.pending
                if b.needs_my_review
            ),
            key=lambda w: (w.created_at, w.id),
        )
        if reviewer
        else []
    )
    counts: dict[str, tuple[int, int] | None] = {}
    pending_by_id = {p.id: p for p in proposals if p.status is ProposalStatus.PENDING}

    def counted(brief: ProposalBrief) -> dict[str, int | None]:
        # ADR 0029: lines of the QUERY diff ((0, 0): metadata only; None: rename / join).
        if brief.id not in counts:
            counts[brief.id] = _apply.line_counts(ctx, pending_by_id[brief.id])
        lines = counts[brief.id]
        return {
            "lines_added": lines[0] if lines else None,
            "lines_removed": lines[1] if lines else None,
        }

    page = LibraryPage(
        rows=[
            row.model_copy(
                update={"pending": [b.model_copy(update=counted(b)) for b in row.pending]}
            )
            for row, _ in kept
        ],
        total=len(rows),
        pending_count=sum(p.status is ProposalStatus.PENDING for p in proposals),
        proposals_visible=with_proposals,
        waiting=[w.model_copy(update=counted(w)) for w in waiting],
    )
    _audit.record(
        ctx,
        user,
        AuditKind.SEARCH,
        fail_closed=False,
        extra={
            "op": "list_library",
            "count": len(kept),
            "needs_my_review": needs_my_review,
            "sort": sort,
        },
    )
    return page


def _order(row: LibraryRow, score: int, sort: str) -> tuple[object, ...]:
    """Sort key (module docstring: `relevance` / `oldest`)."""
    relevance = (-score, _trust_rank(row), row.title.lower(), row.id)
    if sort != "oldest":
        return relevance
    mine = [b.created_at for b in row.pending if b.needs_my_review]
    any_pending = [b.created_at for b in row.pending]
    if mine:
        return (0, min(mine).isoformat(), *relevance)
    if any_pending:
        return (1, min(any_pending).isoformat(), *relevance)
    return (2, "", *relevance)


def _may_decide(user: User, proposal: Proposal, target: Definition | None) -> bool:
    """Whether `user` could approve `proposal` (permission + four-eyes; ADR 0018, 0027)."""
    return can_approve(user, target, author=proposal.author)
