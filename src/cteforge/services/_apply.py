"""Proposal plumbing shared by propose, propose_other, review and library_io (DESIGN §18).

Support module, not a use-case: no permission checks of its own (callers `require` first).

- Building proposals: definition checks via the import engine (services/import_query.py),
  composed-definition checks (services/_compose.py, ADR 0026), owner rule, warehouse
  lookup.
- Submitting: store the proposal, auto-apply it when the team mode and checks allow.
- Applying an approved proposal to the store (`apply_proposal`): a new definition
  version or a join edge, then the join map is refreshed (services/_library.py) and a new
  definition version gets its meaning-search vector (services/_meaning.py; best effort,
  only when the model is already loaded — never fails or delays the apply).

Applied state (ADR 0007): VERIFIED only when a human approver verifies on approval;
otherwise the state the checks earned (LLM_ANNOTATED when they passed and were complete,
DRAFT otherwise). A proposal's stored definition never carries VERIFIED.

Join mining (`_library.refresh_join_map`): every JOIN between two BASE tables of a
definition's SQL (a composed definition's compiled SQL) becomes a JoinEdge with the
deterministic id `_library.join_edge_id`; `sources` and `usage_count` are recomputed after
every apply. Mining never blocks an apply.

Applying claims the proposal first: one compare-and-swap in the store (`update_proposal`
with `expected_status=PENDING`) marks it approved with its resulting version, and only the
winner writes the new version / edge. If that write fails, the claim is released (status
back to pending, best effort). Store writes are not one transaction: a crash between the
claim and the write leaves an approved proposal whose `resulting_version` does not exist.

Drafts (DESIGN §8, §18): a definition proposal whose checks earned DRAFT (a warning such
as `type_unknown`, results not compared, or a metadata-only revision of a draft) is never
auto-applied and can never be applied with verify; a person may still approve it as a
draft. `check_verifiable` is the one rule for verify on approval (also enforced here, not
only by review): no error issue, no `checks_incomplete`, no `draft_not_usable` (a composed
definition on a draft part), and not a DRAFT definition. Join proposals are approved, never
verified (approval by a person confirms the edge).

Joins (DESIGN §18, ADR 0026): a join proposal (`action` add | annotate) is checked on the
default warehouse profile (both tables exist, every key column exists; else a `not_found`
error; no profile or a lookup failure → `checks_incomplete`, so it waits for review); a
relationship claim is checked against the data (`relationship_evidence`: COUNT(*) vs
COUNT(DISTINCT key) per side via compose.relationship; a side claimed "one" with duplicate
keys → `relationship_contradicted` error) and the evidence is stored with the proposal and
the edge. An edge an AGENT adds never auto-applies (trust.approval.join_may_auto_apply).
Applying records a person's approval as `confirmed_by` (trust.approval.confirms_join),
which makes an added edge known. A confirmed edge is treated like a verified definition
(its changes wait for a person or an admin agent). It records the edge it was based on
(`payload.base_join`, None for a new edge); applying it after the edge changed (content,
confirmation or annotations; usage_count and sources ignored) raises StaleVersionError
with a `stale_base_version` issue.

Renames (ADR 0024): a RENAME_DEFINITION proposal (`rename_proposal`) carries `from_id`,
`to_id` and the references seen when it was proposed. It is checked like any change to its
target (auto-applied only in open mode on a definition that is not verified; never with
verify). Applying it re-checks that the definition still has `from_id` (a rename that
another rename overtook is stale) and that `to_id` is still free, then the store renames
it in one transaction (`Store.rename_definition`): no new version, every stored copy of
the id (versions' YAML, composed definitions' parts, proposals) rewritten, the old id
recorded as a former id.

Decisions are serialized in-process (`DECISIONS`): applying and deciding re-read the
proposal under the lock and refuse one that is no longer pending. Across processes on one
database, every decision is a compare-and-swap on the stored status (pending → decided),
so a concurrent approve and reject (or two approves) cannot both succeed: the loser raises
PolicyError.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from cteforge.compose import relationship
from cteforge.compose.checks import ensure_query
from cteforge.domain.access import Tool
from cteforge.domain.audit import AuditKind
from cteforge.domain.codec import AnnotationFile, definition_from_yaml, definition_to_yaml
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import (
    AnnotationError,
    CodecError,
    CteforgeError,
    PermissionDeniedError,
    PolicyError,
    RenderError,
    StaleVersionError,
    StoreError,
    WarehouseError,
)
from cteforge.domain.issues import Issue, IssueCode, Severity, has_errors
from cteforge.domain.models import (
    Annotation,
    Composition,
    Definition,
    DefinitionState,
    JoinEdge,
    RelationshipEvidence,
    SideCount,
    VariableSpec,
)
from cteforge.domain.proposals import Proposal, ProposalKind, ProposalStatus
from cteforge.domain.users import User
from cteforge.parsing.analyze import parse
from cteforge.services import _audit, _compose, _library, _meaning
from cteforge.services.auth import permitted
from cteforge.services.cards import (
    MetadataChange,
    ProposalOutcome,
    ProposalView,
    card_from_definition,
    diff_line_counts,
    json_safe,
    proposal_view,
    unified_diff,
)
from cteforge.services.context import AppContext, team_mode
from cteforge.services.import_query import import_query
from cteforge.trust.approval import (
    can_auto_apply,
    confirms_join,
    join_may_auto_apply,
    reviews_all,
)
from cteforge.warehouse.base import Warehouse

Applied = Definition | JoinEdge

# Serializes proposal decisions in this process (see the module docstring).
DECISIONS = threading.RLock()


# ── Errors and auditing ──────────────────────────────────────────────────────────────


def stale_error(what: str, id: str, latest: int | None, base: int | None) -> StaleVersionError:
    """StaleVersionError carrying a `stale_base_version` Issue on `.issues` (DESIGN §18)."""
    if base is None:
        message = (
            f"{what} '{id}' already exists (version {latest}); use revise with "
            f"base_version={latest} instead."
        )
    else:
        message = (
            f"{what} '{id}' is at version {latest or 'none'}, but this change is based on "
            f"version {base}. Get the latest version and propose the change again."
        )
    error = StaleVersionError(f"stale_base_version: {message}")
    error.issues = [  # type: ignore[attr-defined]
        Issue(code=IssueCode.STALE_BASE_VERSION, severity=Severity.ERROR, message=message)
    ]
    return error


def stale_join_error(id: str) -> StaleVersionError:
    """StaleVersionError for a join proposal whose edge changed since it was proposed."""
    message = (
        f"Join '{id}' changed since this proposal was made (content or state). Get the "
        "current edge and propose the change again."
    )
    error = StaleVersionError(f"stale_base_version: {message}")
    error.issues = [  # type: ignore[attr-defined]
        Issue(code=IssueCode.STALE_BASE_VERSION, severity=Severity.ERROR, message=message)
    ]
    return error


def record_proposal(
    ctx: AppContext, user: User, kind: AuditKind, proposal: Proposal, op: str
) -> None:
    """Success audit event for a proposal-related call (fail closed)."""
    applied = proposal.resulting_version is not None
    definitions = (
        [f"{proposal.target_id}@{proposal.resulting_version}"]
        if applied
        and proposal.kind in (ProposalKind.NEW_DEFINITION, ProposalKind.REVISE_DEFINITION)
        else []
    )
    extra: dict[str, Any] = {
        "op": op,
        "proposal_id": proposal.id,
        "proposal_kind": proposal.kind.value,
        "target_id": proposal.target_id,
        "status": proposal.status.value,
        "auto_applied": proposal.auto_applied,
    }
    if proposal.kind is ProposalKind.RENAME_DEFINITION:
        # Listed under both ids, so the audit history of either finds the rename.
        old, new = str(proposal.payload["from_id"]), str(proposal.payload["to_id"])
        version = proposal.base_version
        definitions = [f"{old}@{version}", f"{new}@{version}"]
        extra |= {"renamed_from": old, "renamed_to": new}
    _audit.record(ctx, user, kind, definitions=definitions, extra=extra)


# ── Inputs ───────────────────────────────────────────────────────────────────────────


def parse_annotations(items: Sequence[Annotation | Mapping[str, Any]]) -> list[Annotation]:
    out: list[Annotation] = []
    for i, item in enumerate(items):
        if isinstance(item, Annotation):
            out.append(item)
            continue
        try:
            out.append(Annotation.model_validate(item))
        except ValidationError as exc:
            raise AnnotationError(
                f"Invalid annotation #{i + 1}: {validation_detail(exc)}"
            ) from None
    return out


def validation_detail(exc: ValidationError) -> str:
    """Field-located pydantic errors as one line (no input values)."""
    return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())


def check_owner(user: User, proposed: str | None, current: str | None) -> None:
    """`owner` may be set only to the proposer (or left empty), and only when nobody else
    owns the definition; reviewers of everything (review_all: built-in approvers and
    admins) may set any owner (ADR 0018, 0027)."""
    if proposed == current or reviews_all(user):
        return
    if proposed in (None, user.name) and current in (None, user.name):
        return
    raise PermissionDeniedError(
        f"User '{user.name}' may set owner only to themselves on a definition nobody else "
        "owns; ask an approver to change the owner."
    )


def check_join_keys(definition: Definition) -> None:
    outputs = {o.lower() for o in definition.outputs}
    missing = [k for k in definition.join_keys if k.lower() not in outputs]
    if missing:
        raise CodecError(
            f"join_keys {missing} are not output columns of '{definition.id}' "
            f"(outputs: {definition.outputs})"
        )


def warehouse_for(ctx: AppContext, name: str | None) -> tuple[str | None, Warehouse | None]:
    """(profile name, warehouse) to run checks on. No profiles configured and none named →
    (None, None): checks then stay incomplete and the proposal is never auto-applied."""
    if name is None and not ctx.warehouses.names():
        return None, None
    resolved = ctx.warehouses.resolve_name(name)
    return resolved, ctx.warehouses.get(resolved)


# ── Definition checks ────────────────────────────────────────────────────────────────


def ensure_read_only(sql: str, dialect: Dialect) -> None:
    """Refuse a proposed definition's SQL unless it is one read-only SELECT/UNION
    (compose.checks.ensure_query), before any check touches the warehouse. Raises
    PolicyError carrying a `not_read_only` Issue on `.issues`; SqlParseError when the SQL
    does not parse as one statement."""
    try:
        ensure_query(parse(sql, dialect), "definition's SQL")
    except RenderError as exc:
        message = str(exc)
        error = PolicyError(f"not_read_only: {message}")
        error.issues = [  # type: ignore[attr-defined]
            Issue(code=IssueCode.NOT_READ_ONLY, severity=Severity.ERROR, message=message)
        ]
        raise error from None


def checked_definition(
    sql: str,
    dialect: Dialect,
    annotations: list[Annotation],
    metadata: Mapping[str, Any],
    warehouse: Warehouse | None,
) -> tuple[Definition, list[Issue]]:
    """Run the checks engine (DESIGN §7) and return the definition with `metadata` (id,
    title, meaning, keywords, use_when, dont_use_when, grain, join_keys, owner, profile,
    version) applied, plus the issues. State is what the checks earned, never VERIFIED.
    Non-read-only SQL is refused first (`ensure_read_only`, PolicyError), so it never
    reaches EXPLAIN or a check run. Raises AnnotationError / SqlParseError when no
    template can be built."""
    ensure_read_only(sql, dialect)
    header = {k: metadata[k] for k in AnnotationFile.model_fields if k in metadata}
    try:
        ann_file = AnnotationFile.model_validate(header | {"variables": annotations})
    except ValidationError as exc:
        raise CodecError(f"Invalid definition metadata: {validation_detail(exc)}") from None
    outcome = import_query(sql, dialect, ann_file, warehouse)
    extra = {k: metadata[k] for k in ("join_keys", "owner", "profile", "version") if k in metadata}
    try:
        definition = Definition.model_validate(outcome.definition.model_dump() | extra)
    except ValidationError as exc:
        raise CodecError(f"Invalid definition metadata: {validation_detail(exc)}") from None
    check_join_keys(definition)
    return definition, list(outcome.issues)


def checked_composition(
    ctx: AppContext,
    user: User,
    composition: Composition,
    select: str | None,
    dialect: Dialect,
    annotations: list[Annotation],
    metadata: Mapping[str, Any],
    warehouse: Warehouse | None,
) -> tuple[Definition, list[Issue]]:
    """The composed-definition counterpart of `checked_definition` (ADR 0026):
    `_compose.checked_composition`, then the join-keys rule."""
    definition, issues = _compose.checked_composition(
        ctx, user, composition, select, dialect, annotations, metadata, warehouse
    )
    check_join_keys(definition)
    return definition, issues


def logic_changed(old: Definition, new: Definition) -> bool:
    """DESIGN §15: SQL (or composition), variables, grain, outputs or join keys changed."""
    keys = (
        "source_dialect",
        "template",
        "variables",
        "composition",
        "grain",
        "outputs",
        "join_keys",
        "tables",
    )
    return any(getattr(old, k) != getattr(new, k) for k in keys)


# ── Submitting ───────────────────────────────────────────────────────────────────────


def new_proposal(
    ctx: AppContext,
    user: User,
    kind: ProposalKind,
    target_id: str,
    payload: dict[str, Any],
    checks: list[Issue],
    *,
    summary: str = "",
    base_version: int | None = None,
) -> Proposal:
    return Proposal(
        id=uuid.uuid4().hex,
        kind=kind,
        target_id=target_id,
        base_version=base_version,
        payload=payload,
        checks=checks,
        summary=summary,
        author=user.name,
        author_kind=user.kind,
        created_at=ctx.now(),
    )


def definition_payload(definition: Definition, *, changed_logic: bool) -> dict[str, Any]:
    stored = definition.model_copy(update={"verified_by": None, "verified_at": None})
    if stored.state is DefinitionState.VERIFIED:
        stored = stored.model_copy(update={"state": DefinitionState.LLM_ANNOTATED})
    return {"definition_yaml": definition_to_yaml(stored), "logic_changed": changed_logic}


def rename_proposal(
    ctx: AppContext, user: User, current: Definition, new_id: str, summary: str
) -> Proposal:
    """A RENAME_DEFINITION proposal of `current` to `new_id` (ADR 0024). Callers validate
    `new_id` (`_library.check_id_available`). The payload keeps the references seen now,
    so a reviewer sees what the proposer saw; `base_version` is informational (a rename
    keeps every version, so a later revision does not make it stale)."""
    references = _library.definition_references(ctx, current)
    return new_proposal(
        ctx,
        user,
        ProposalKind.RENAME_DEFINITION,
        current.id,
        {
            "from_id": current.id,
            "to_id": new_id,
            "references": references.model_dump(mode="json"),
        },
        [],
        summary=summary.strip() or f"Rename {current.id} to {new_id}",
        base_version=current.version,
    )


def stale_rename_error(proposal: Proposal) -> StaleVersionError:
    """StaleVersionError for a rename whose definition no longer has `from_id`."""
    old = str(proposal.payload.get("from_id"))
    now = proposal.target_id
    message = (
        f"This rename was proposed for '{old}', which has since been renamed to '{now}'. "
        f"Reject it and propose the rename again from '{now}'."
        if now != old
        else f"Definition '{old}' no longer exists; this rename can't be applied."
    )
    error = StaleVersionError(f"stale_base_version: {message}")
    error.issues = [  # type: ignore[attr-defined]
        Issue(code=IssueCode.STALE_BASE_VERSION, severity=Severity.ERROR, message=message)
    ]
    return error


_JOIN_BASIS_IGNORED = {"usage_count", "sources", "trusted_sources"}
# The annotations a person confirms (ADR 0026): changing them un-confirms an edge unless a
# person approves the change.
_JOIN_ANNOTATIONS = ("relationship", "traps", "notes")


def _join_basis(edge: JoinEdge | None) -> dict[str, Any] | None:
    """What a join proposal is based on: the edge without what mining recomputes."""
    return None if edge is None else edge.model_dump(mode="json", exclude=_JOIN_BASIS_IGNORED)


def _incomplete(message: str) -> Issue:
    return Issue(code=IssueCode.CHECKS_INCOMPLETE, severity=Severity.WARNING, message=message)


def _not_found(message: str) -> Issue:
    return Issue(code=IssueCode.NOT_FOUND, severity=Severity.ERROR, message=message)


def _default_warehouse(ctx: AppContext) -> tuple[str | None, Warehouse | None]:
    try:
        return warehouse_for(ctx, None)
    except CteforgeError:
        return None, None


def join_checks(ctx: AppContext, edge: JoinEdge) -> list[Issue]:
    """Both tables and every key column exist on the default warehouse profile. Without a
    usable profile the checks are incomplete (the proposal then waits for review)."""
    _, warehouse = _default_warehouse(ctx)
    if warehouse is None:
        return [
            _incomplete(
                "No default warehouse profile: the join's tables and key columns were not checked."
            )
        ]
    issues: list[Issue] = []
    sides = (
        (edge.left_table, [k.left for k in edge.keys]),
        (edge.right_table, [k.right for k in edge.keys]),
    )
    for table, wanted in sides:
        try:
            columns = {c.lower() for c in warehouse.column_types(table)}
        except WarehouseError:
            issues.append(_incomplete(f"Could not read the columns of '{table}'."))
            continue
        if not columns:
            issues.append(_not_found(f"Table '{table}' does not exist on the warehouse."))
            continue
        missing = sorted({c for c in wanted if c.lower() not in columns})
        if missing:
            issues.append(_not_found(f"Key column(s) {missing} do not exist in '{table}'."))
    return issues


def _count_side(warehouse: Warehouse, table: str, keys: list[str]) -> SideCount:
    """Rows with every key set vs distinct key tuples on one side. The query is built by
    compose.relationship from sqlglot identifiers (no text splicing, no values)."""
    result = warehouse.run(relationship.relationship_query(table, keys, warehouse.dialect), 1)
    if len(result.rows) != 1 or len(result.rows[0]) != 2:
        raise WarehouseError("unexpected result shape")
    rows, distinct = (int(v or 0) for v in result.rows[0])
    return SideCount(table=table, keys=keys, rows=rows, distinct_keys=distinct)


def relationship_evidence(
    ctx: AppContext, edge: JoinEdge
) -> tuple[RelationshipEvidence | None, list[Issue]]:
    """Check `edge.relationship` against the default warehouse (ADR 0026): COUNT(*) vs
    COUNT(DISTINCT key) on each side. A side the claim calls "one" that has duplicate keys
    is a `relationship_contradicted` error; no warehouse or a failed count →
    `checks_incomplete` (a runtime failure keeps only its class: messages can quote
    data). Returns (evidence, issues); no relationship → (None, [])."""
    if edge.relationship is None:
        return None, []
    profile, warehouse = _default_warehouse(ctx)
    if warehouse is None:
        return None, [
            _incomplete("No default warehouse profile: the relationship was not checked.")
        ]
    try:
        left = _count_side(warehouse, edge.left_table, [k.left for k in edge.keys])
        right = _count_side(warehouse, edge.right_table, [k.right for k in edge.keys])
    except (WarehouseError, RenderError, ValueError, TypeError) as exc:
        return None, [
            _incomplete(
                f"The relationship could not be checked on the warehouse ({type(exc).__name__})."
            )
        ]
    observed = relationship.relationship_of(
        relationship.side(left.rows, left.distinct_keys),
        relationship.side(right.rows, right.distinct_keys),
    )
    fits = relationship.consistent(edge.relationship, observed)
    evidence = RelationshipEvidence(
        claimed=edge.relationship,
        observed=observed,
        left=left,
        right=right,
        consistent=fits,
        profile=profile,
        checked_at=ctx.now(),
    )
    if fits:
        return evidence, []
    message = (
        f"The data contradicts '{edge.relationship}': '{left.table}' has {left.rows} rows for "
        f"{left.distinct_keys} distinct keys and '{right.table}' {right.rows} rows for "
        f"{right.distinct_keys} distinct keys, so it is {observed} now. A side called "
        "'one' must have each key once."
    )
    return evidence, [
        Issue(code=IssueCode.RELATIONSHIP_CONTRADICTED, severity=Severity.ERROR, message=message)
    ]


def join_proposal(
    ctx: AppContext, user: User, edge: JoinEdge, summary: str, *, action: str = "annotate"
) -> Proposal:
    """A NEW_JOIN proposal (`action` add: a new edge; annotate: relationship / traps /
    notes on an existing edge) for a canonical edge, with its checks (`join_checks`, and
    the relationship evidence when a relationship is claimed) and the edge it is based on
    (`payload.base_join`)."""
    existing = ctx.store.get_join(edge.id)
    keep: dict[str, Any] = {"usage_count": 0, "sources": [], "trusted_sources": []}
    if existing is not None:
        keep = {
            "usage_count": existing.usage_count,
            "sources": list(existing.sources),
            "trusted_sources": list(existing.trusted_sources),
            "added_by": existing.added_by,
            "added_by_kind": existing.added_by_kind,
            "confirmed_by": existing.confirmed_by,
        }
    elif action == "add":
        keep |= {"added_by": user.name, "added_by_kind": user.kind.value, "confirmed_by": None}
    edge = edge.model_copy(update=keep)
    checks = join_checks(ctx, edge)
    evidence: RelationshipEvidence | None = None
    claim_changed = edge.relationship is not None and (
        existing is None or edge.relationship != existing.relationship
    )
    if claim_changed and not permitted(user, Tool.CHECK_AND_RUN):
        # Counting rows of named tables and columns is exploratory SQL: without
        # check_and_run the proposer would get a row-count oracle (security review).
        checks.append(
            _incomplete(
                "The relationship was not checked against the data: checking it counts rows "
                "on the warehouse, which needs the check_and_run tool the proposer does not "
                "hold. A reviewer can still decide."
            )
        )
    elif claim_changed and not checks:
        # Only when join_checks positively confirmed both tables and every key column.
        evidence, evidence_issues = relationship_evidence(ctx, edge)
        checks += evidence_issues
    elif claim_changed:
        checks.append(
            _incomplete(
                "The relationship was not checked against the data: the tables and key "
                "columns could not be confirmed first."
            )
        )
    if evidence is not None or (
        existing is not None and edge.relationship != existing.relationship
    ):
        edge = edge.model_copy(update={"evidence": evidence})
    return new_proposal(
        ctx,
        user,
        ProposalKind.NEW_JOIN,
        edge.id,
        {
            "join": edge.model_dump(mode="json"),
            "base_join": _join_basis(existing),
            "action": action,
            "evidence": evidence.model_dump(mode="json") if evidence is not None else None,
        },
        checks,
        summary=summary,
    )


def target_state(ctx: AppContext, proposal: Proposal) -> DefinitionState | None:
    """Current state of what the proposal changes; None when it does not exist yet. A join
    edge a person confirmed counts as VERIFIED (changes to it wait for a person or an
    admin agent), any other edge as LLM_ANNOTATED (ADR 0026)."""
    if proposal.kind is ProposalKind.NEW_RECIPE:
        return None  # legacy: recipes no longer exist
    if proposal.kind is ProposalKind.NEW_JOIN:
        edge = ctx.store.get_join(proposal.target_id)
        if edge is None:
            return None
        if edge.confirmed_by is not None:
            return DefinitionState.VERIFIED
        return DefinitionState.LLM_ANNOTATED
    definition = ctx.store.get_definition(proposal.target_id)
    return None if definition is None else definition.state


def proposed_state(proposal: Proposal) -> DefinitionState | None:
    """The state a definition proposal's checks earned; None for joins, renames and legacy
    recipes."""
    if proposal.kind not in (ProposalKind.NEW_DEFINITION, ProposalKind.REVISE_DEFINITION):
        return None
    return definition_from_yaml(str(proposal.payload["definition_yaml"])).state


def may_auto_apply(ctx: AppContext, proposal: Proposal) -> bool:
    """DESIGN §18 via trust.approval.can_auto_apply: open mode, no errors, complete checks,
    target new or not verified (a join: not confirmed), and not a DRAFT definition (its
    checks did not all pass). An edge an agent adds never auto-applies (ADR 0026)."""
    if proposal.kind is ProposalKind.NEW_RECIPE:
        return False
    if proposal.kind is ProposalKind.NEW_JOIN and not join_may_auto_apply(
        str(proposal.payload.get("action", "annotate")), proposal.author_kind
    ):
        return False
    state = target_state(ctx, proposal)
    if state is DefinitionState.VERIFIED:
        return False
    if proposed_state(proposal) is DefinitionState.DRAFT:
        return False
    return can_auto_apply(team_mode(ctx), None, proposal.checks)


def verify_refusal(proposal: Proposal) -> str | None:
    """Why `proposal` may not be applied with verify (None when it may). A verified result
    must rest on complete, passing checks (ADR 0007, DESIGN §8)."""
    codes = {i.code for i in proposal.checks}
    if proposal.kind is ProposalKind.RENAME_DEFINITION:
        return "is a rename; there is nothing to verify (approve it without verify)"
    if proposal.kind is ProposalKind.NEW_JOIN:
        return "is a join; joins are approved, not verified (a person's approval confirms the edge)"
    if proposal.kind is ProposalKind.NEW_RECIPE:
        return "is a recipe proposal; recipes no longer exist (ADR 0026)"
    if has_errors(proposal.checks):
        return "has failing checks; it cannot be approved or verified"
    if IssueCode.CHECKS_INCOMPLETE in codes:
        return "was not fully checked (checks_incomplete); it can be approved but not verified"
    if IssueCode.DRAFT_NOT_USABLE in codes:
        return "uses a draft definition; it can be approved but not verified"
    if IssueCode.PART_NOT_USABLE in codes:
        return "uses a part that is not usable now; it can be approved but not verified"
    if proposed_state(proposal) is DefinitionState.DRAFT:
        return (
            "is a draft (its checks did not all pass); it can be approved as a draft but not "
            "verified. Revise it so the checks pass"
        )
    return None


def check_verifiable(proposal: Proposal) -> None:
    """PolicyError unless `proposal` may be applied with verify (`verify_refusal`)."""
    reason = verify_refusal(proposal)
    if reason is not None:
        raise PolicyError(f"Proposal '{proposal.id}' {reason}.")


def submit(ctx: AppContext, user: User, proposal: Proposal) -> ProposalOutcome:
    """Store the proposal; auto-apply it when allowed. Returns the outcome."""
    ctx.store.add_proposal(proposal)
    applied: Applied | None = None
    if may_auto_apply(ctx, proposal):
        proposal, applied = apply_proposal(ctx, proposal, user, auto=True)
    return outcome(ctx, proposal, applied)


def outcome(ctx: AppContext, proposal: Proposal, applied: Applied | None) -> ProposalOutcome:
    card = card_from_definition(applied) if isinstance(applied, Definition) else None
    return ProposalOutcome(
        proposal=view(ctx, proposal),
        definition=card,
        issues=list(proposal.checks),
        auto_applied=proposal.auto_applied,
    )


def view(ctx: AppContext, proposal: Proposal, *, full_diff: bool = False) -> ProposalView:
    """Proposal + diff against its base (`full_diff`: with every unchanged line, ADR 0029)
    + impact preview ("used by": composed definitions using the target definition, ADR
    0026) + for a composed definition its parts, fixed/open variables and compiled SQL +
    its title (ADR 0029: the proposed definition's; a rename's: the stored target's) + the
    query diff and the described fields that change (ADR 0029: `sql_diff`,
    `metadata_changes`; definition proposals only)."""
    base: Applied | None = None
    impact: list[str] = []
    composition = None
    title: str | None = None
    sql: str | None = None
    changes: list[MetadataChange] | None = None
    if proposal.kind in (ProposalKind.NEW_DEFINITION, ProposalKind.REVISE_DEFINITION):
        if proposal.base_version is not None:
            base = ctx.store.get_definition(proposal.target_id, proposal.base_version)
        impact = [d.id for d in ctx.store.definitions_using(proposal.target_id)]
        proposed = _proposed_definition(proposal)
        if proposed is not None and proposed.composition is not None:
            composition = _compose.composition_card(ctx, proposed)
        title = proposed.title if proposed is not None else _stored_title(ctx, proposal)
        old = base if isinstance(base, Definition) else None
        sql = query_diff(proposal, old, proposed, full=full_diff)
        changes = metadata_changes(old, proposed)
    elif proposal.kind is ProposalKind.RENAME_DEFINITION:
        # No diff: the payload says from → to; impact is what uses the definition now.
        impact = [d.id for d in ctx.store.definitions_using(proposal.target_id)]
        title = _stored_title(ctx, proposal)
    elif proposal.kind is ProposalKind.NEW_JOIN:
        base = ctx.store.get_join(proposal.target_id)
    return proposal_view(
        proposal,
        base,
        impact,
        composition,
        full_diff=full_diff,
        title=title,
        sql_diff=sql,
        metadata_changes=changes,
    )


def _stored_title(ctx: AppContext, proposal: Proposal) -> str | None:
    """The target definition's latest title (or None when it is not stored)."""
    stored = ctx.store.get_definition(proposal.target_id)
    return stored.title if stored is not None else None


def _proposed_definition(proposal: Proposal) -> Definition | None:
    try:
        return definition_from_yaml(str(proposal.payload["definition_yaml"]))
    except (CodecError, KeyError):
        return None


def query_text(definition: Definition | None) -> str:
    """The trusted query as a reviewer reads it (ADR 0029): the source SQL as written —
    the template rendered with its original values, so both sides of a diff are formatted
    alike —; for a composed definition its parts (alias → id, pinned version or latest,
    fixed values) and its final SELECT. "" for none."""
    if definition is None:
        return ""
    lines: list[str] = []
    if definition.composition is not None:
        for alias, ref in definition.composition.uses.items():
            pin = f" v{ref.version}" if ref.version is not None else " (latest)"
            lines.append(f"-- part {alias}: {ref.id}{pin}")
            for var, value in definition.composition.fixed.get(alias, {}).items():
                lines.append(f"--   fixed {alias}.{var} = {json.dumps(value, default=str)}")
    try:
        body = _library.original_sql(definition)
    except CteforgeError:
        body = definition.template  # cannot be rendered: show it as stored
    if body.strip():
        lines.extend(body.rstrip("\n").splitlines())
    return "\n".join(lines) + ("\n" if lines else "")


def query_diff(
    proposal: Proposal,
    base: Definition | None,
    proposed: Definition | None,
    *,
    full: bool = False,
) -> str:
    """Unified diff of `query_text` base → proposed (ADR 0029); "" when the query is the
    same (a metadata-only change)."""
    old_name = "/dev/null" if base is None else f"{proposal.target_id}@v{base.version}.sql"
    return unified_diff(
        query_text(base),
        query_text(proposed),
        old_name,
        f"{proposal.target_id} (proposed).sql",
        full=full,
    )


# The described fields a reviewer compares (ADR 0029). Never version / state / verifier /
# schema_version / tables / outputs (derived from the SQL, shown by the query diff).
_DESCRIBED = (
    "title",
    "meaning",
    "keywords",
    "use_when",
    "dont_use_when",
    "grain",
    "join_keys",
    "owner",
    "profile",
    "source_dialect",
)


def _variable_text(v: VariableSpec) -> str:
    text = f"{v.name}: {v.type.value} {v.role.value}"
    if v.allowed:
        text += f", allowed {', '.join(str(a) for a in v.allowed)}"
    if v.unit or v.format:
        text += f", {v.unit or v.format}"
    if v.description:
        text += f" — {v.description}"
    return text


def metadata_changes(base: Definition | None, proposed: Definition | None) -> list[MetadataChange]:
    """Described fields that differ base → proposed (a new definition: every set field,
    before None), and the variables as one entry per side."""
    if proposed is None:
        return []
    changes: list[MetadataChange] = []
    for name in _DESCRIBED:
        before = json_safe(getattr(base, name)) if base is not None else None
        after = json_safe(getattr(proposed, name))
        if base is None and after in (None, "", []):
            continue
        if before != after:
            changes.append(MetadataChange(field=name, before=before, after=after))
    before_vars = [_variable_text(v) for v in base.variables] if base is not None else None
    after_vars = [_variable_text(v) for v in proposed.variables]
    if before_vars != after_vars and not (base is None and not after_vars):
        changes.append(MetadataChange(field="variables", before=before_vars, after=after_vars))
    return changes


def line_counts(ctx: AppContext, proposal: Proposal) -> tuple[int, int] | None:
    """(added, removed) lines of the proposal's QUERY diff (ADR 0029: the trusted SQL, or a
    composed definition's parts and final SELECT) — (0, 0) for a metadata-only change.
    None for renames and joins (no query)."""
    if proposal.kind not in (ProposalKind.NEW_DEFINITION, ProposalKind.REVISE_DEFINITION):
        return None
    base = None
    if proposal.base_version is not None:
        base = ctx.store.get_definition(proposal.target_id, proposal.base_version)
    return diff_line_counts(query_diff(proposal, base, _proposed_definition(proposal)))


# ── Applying ─────────────────────────────────────────────────────────────────────────


def decide(
    ctx: AppContext,
    proposal: Proposal,
    user: User,
    status: ProposalStatus,
    *,
    comment: str = "",
    auto: bool = False,
    resulting_version: int | None = None,
) -> Proposal:
    """Record a decision on a proposal (status, who, when, comment) in the store, as a
    compare-and-swap from pending (PolicyError when it is no longer pending)."""
    with DECISIONS:
        still_pending(ctx, proposal.id)
        return _decide(ctx, proposal, user, status, comment, auto, resulting_version)


def still_pending(ctx: AppContext, id: str) -> Proposal:
    """The stored proposal, which must be pending (PolicyError otherwise)."""
    stored = ctx.store.get_proposal(id)
    if stored is None or stored.status is not ProposalStatus.PENDING:
        status = "missing" if stored is None else stored.status.value
        raise PolicyError(f"Proposal '{id}' is {status}; only pending proposals can be decided")
    return stored


def _decide(
    ctx: AppContext,
    proposal: Proposal,
    user: User,
    status: ProposalStatus,
    comment: str,
    auto: bool,
    resulting_version: int | None,
) -> Proposal:
    """Compare-and-swap pending → `status`. Another process that decided first makes this
    raise PolicyError (the stored proposal is no longer pending)."""
    decided = proposal.model_copy(
        update={
            "status": status,
            "auto_applied": auto,
            "decided_by": user.name,
            "decided_by_kind": user.kind,
            "decided_at": ctx.now(),
            "comment": comment,
            "resulting_version": resulting_version,
        }
    )
    try:
        return ctx.store.update_proposal(decided, expected_status=ProposalStatus.PENDING)
    except StoreError:
        stored = ctx.store.get_proposal(proposal.id)
        if stored is not None and stored.status is not ProposalStatus.PENDING:
            raise PolicyError(
                f"Proposal '{proposal.id}' was {stored.status.value} meanwhile (by "
                f"{stored.decided_by}); only pending proposals can be decided"
            ) from None
        raise


def _release(ctx: AppContext, original: Proposal) -> None:
    """Undo our claim after its write failed: approved → pending again (best effort). Only
    the claim can have made it approved (approved is final), so the CAS restores it."""
    try:
        ctx.store.update_proposal(original, expected_status=ProposalStatus.APPROVED)
    except CteforgeError:
        pass


def _applied_state(proposed: DefinitionState, verify: bool) -> DefinitionState:
    if verify:
        return DefinitionState.VERIFIED
    return DefinitionState.LLM_ANNOTATED if proposed is DefinitionState.VERIFIED else proposed


def _check_base(ctx: AppContext, proposal: Proposal) -> None:
    """StaleVersionError (stale_base_version issue) when the target moved on since the
    proposal was made."""
    if proposal.kind in (ProposalKind.NEW_DEFINITION, ProposalKind.REVISE_DEFINITION):
        latest = ctx.store.latest_version(proposal.target_id)
        if latest != proposal.base_version:
            raise stale_error("Definition", proposal.target_id, latest, proposal.base_version)
        # Another composed definition applied meanwhile may close a cycle (ADR 0026).
        cycle = _compose.cycle_issue(
            ctx, definition_from_yaml(str(proposal.payload["definition_yaml"]))
        )
        if cycle is not None:
            error = PolicyError(f"composition_cycle: {cycle.message}")
            error.issues = [cycle]  # type: ignore[attr-defined]
            raise error
    elif proposal.kind is ProposalKind.NEW_RECIPE:
        raise PolicyError("Recipes no longer exist (ADR 0026); this proposal can't be applied.")
    elif proposal.kind is ProposalKind.RENAME_DEFINITION:
        old = str(proposal.payload["from_id"])
        if proposal.target_id != old or ctx.store.latest_version(old) is None:
            raise stale_rename_error(proposal)
        _library.check_id_available(ctx, str(proposal.payload["to_id"]), renaming=old)
    # Proposals stored before base_join existed carry no basis and are not checked.
    elif "base_join" in proposal.payload:
        current_edge = _join_basis(ctx.store.get_join(proposal.target_id))
        if current_edge != proposal.payload["base_join"]:
            raise stale_join_error(proposal.target_id)


def _resulting_version(proposal: Proposal) -> int | None:
    """The version applying creates (base + 1); None for a join edge or a rename (a rename
    creates no version, ADR 0024)."""
    if proposal.kind in (ProposalKind.NEW_JOIN, ProposalKind.RENAME_DEFINITION):
        return None
    return (proposal.base_version or 0) + 1


def _lost_race(what: str, proposal: Proposal, latest: int | None) -> StaleVersionError:
    return stale_error(what, proposal.target_id, latest, proposal.base_version)


def _apply_definition(
    ctx: AppContext, proposal: Proposal, user: User, verify: bool, version: int
) -> Definition:
    proposed = definition_from_yaml(str(proposal.payload["definition_yaml"]))
    if verify and proposed.state is DefinitionState.DRAFT:
        raise PolicyError(
            f"Proposal '{proposal.id}' is a draft (its checks did not all pass); it can be "
            "approved as a draft but not verified."
        )
    definition = proposed.model_copy(
        update={
            "version": version,
            "state": _applied_state(proposed.state, verify),
            "verified_by": user.name if verify else None,
            "verified_at": ctx.now().date() if verify else None,
        }
    )
    try:
        return ctx.store.add_definition_version(
            definition,
            author=proposal.author,
            change_note=proposal.summary,
            logic_changed=bool(proposal.payload.get("logic_changed", True)),
        )
    except StaleVersionError:  # another process wrote this version first
        raise _lost_race(
            "Definition", proposal, ctx.store.latest_version(proposal.target_id)
        ) from None


def _apply_join(ctx: AppContext, proposal: Proposal, user: User, auto: bool) -> JoinEdge:
    """Write the proposed edge. Mining-owned fields (sources, trusted_sources, usage_count)
    are taken from the stored edge. A person's approval confirms it (`confirmed_by`);
    any other approval of a change to its relationship, traps or notes un-confirms it (a
    confirmation covers what the person saw; ADR 0026)."""
    proposed = JoinEdge.model_validate(proposal.payload["join"])
    current = ctx.store.get_join(proposed.id)
    update: dict[str, Any] = {}
    if current is not None:
        update |= {
            "sources": list(current.sources),
            "trusted_sources": list(current.trusted_sources),
            "usage_count": current.usage_count,
            "confirmed_by": current.confirmed_by,
        }
    if confirms_join(user.kind, auto=auto):
        update["confirmed_by"] = user.name
    elif current is not None and any(
        getattr(current, k) != getattr(proposed, k) for k in _JOIN_ANNOTATIONS
    ):
        update["confirmed_by"] = None
    edge = proposed.model_copy(update=update)
    ctx.store.upsert_join(edge)
    return edge


def apply_proposal(
    ctx: AppContext,
    proposal: Proposal,
    user: User,
    *,
    auto: bool,
    verify: bool = False,
    comment: str = "",
) -> tuple[Proposal, Applied]:
    """Apply a pending proposal and mark it approved by `user`. Callers check permissions;
    verify is refused here too unless `check_verifiable` passes. Raises StaleVersionError
    (with a stale_base_version issue) when the target moved on since the proposal was made,
    PolicyError when the proposal was decided meanwhile (in this or another process)."""
    if proposal.status is not ProposalStatus.PENDING:
        raise PolicyError(f"Proposal '{proposal.id}' is {proposal.status}, not pending")
    if verify:
        check_verifiable(proposal)
    with DECISIONS:
        still_pending(ctx, proposal.id)
        _check_base(ctx, proposal)
        version = _resulting_version(proposal)
        # Claim first (compare-and-swap): only the winner writes.
        decided = _decide(ctx, proposal, user, ProposalStatus.APPROVED, comment, auto, version)
        applied: Applied
        try:
            if proposal.kind in (ProposalKind.NEW_DEFINITION, ProposalKind.REVISE_DEFINITION):
                assert version is not None
                applied = _apply_definition(ctx, proposal, user, verify, version)
            elif proposal.kind is ProposalKind.RENAME_DEFINITION:
                applied = ctx.store.rename_definition(
                    str(proposal.payload["from_id"]),
                    str(proposal.payload["to_id"]),
                    renamed_by=user.name,
                    renamed_at=ctx.now(),
                    proposal_id=proposal.id,
                )
            else:
                applied = _apply_join(ctx, proposal, user, auto)
        except BaseException:
            _release(ctx, proposal)
            raise
        # The change is in the library; the join map follows (mining never blocks it).
        _library.refresh_join_map(ctx, lambda d: _compose.compiled_sql(ctx, d))
        if isinstance(applied, JoinEdge):
            edge = ctx.store.get_join(applied.id)
            assert edge is not None
            applied = edge
    if proposal.kind is ProposalKind.RENAME_DEFINITION:
        # The store moved this proposal's target to the new id: return it as stored.
        decided = ctx.store.get_proposal(proposal.id) or decided
    if isinstance(applied, Definition):
        # Meaning search vector (ADR 0023): best effort, never fails or delays the apply
        # (only embeds when the model is already loaded; search backfills otherwise).
        _meaning.index_definition(ctx, applied)
    return decided, applied
