"""Proposal plumbing shared by propose, propose_other, review and library_io (DESIGN §18).

Support module, not a use-case: no permission checks of its own (callers `require` first).

- Building proposals: definition checks via the import engine (services/import_query.py),
  recipe checks (render + compose dry-run, nothing runs), owner rule, warehouse lookup.
- Submitting: store the proposal, auto-apply it when the team mode and checks allow.
- Applying an approved proposal to the store (`apply_proposal`): a new definition/recipe
  version or a join edge, then the join map is refreshed (services/_library.py).

Applied state (ADR 0007): VERIFIED only when a human approver verifies on approval;
otherwise the state the checks earned (LLM_ANNOTATED when they passed and were complete,
DRAFT otherwise). A proposal's stored definition never carries VERIFIED.

Join mining (`_library.refresh_join_map`): every JOIN between two BASE tables of a
definition's SQL becomes a JoinEdge with the deterministic id `_library.join_edge_id`;
`usage_count` is recomputed after every apply. Mining never blocks an apply.

Store writes are not one transaction: a definition version is written before its proposal
is marked approved. A crash between the two leaves a pending proposal whose re-approval
fails as stale.

Drafts (DESIGN §8, §18): a definition proposal whose checks earned DRAFT (a warning such
as `type_unknown`, results not compared, or a metadata-only revision of a draft) is never
auto-applied and can never be applied with verify; a person may still approve it as a
draft.

Joins (DESIGN §18): a join proposal is checked on the default warehouse profile (both
tables exist, every key column exists; else a `not_found` error; no profile or a lookup
failure → `checks_incomplete`, so it waits for review). It records the edge it was based
on (`payload.base_join`, None for a new edge); applying it after the edge changed (content
or state, usage_count ignored) raises StaleVersionError with a `stale_base_version` issue.

Decisions are serialized in-process (`DECISIONS`): applying and deciding re-read the
proposal under the lock and refuse one that is no longer pending, so a concurrent approve
and reject (or two approves) cannot both succeed. Separate server processes on one
database are not covered (that needs a compare-and-swap in the store).
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import TypeAdapter, ValidationError

from cteforge.compose.assemble import Part, compose
from cteforge.compose.checks import ensure_query
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
    SqlParseError,
    StaleVersionError,
    VariableValueError,
    WarehouseError,
)
from cteforge.domain.issues import Issue, IssueCode, Severity, has_errors
from cteforge.domain.models import (
    Annotation,
    Definition,
    DefinitionState,
    Ident,
    JoinEdge,
    Recipe,
)
from cteforge.domain.proposals import Proposal, ProposalKind, ProposalStatus
from cteforge.domain.users import User, UserRole, role_at_least
from cteforge.parsing.analyze import parse
from cteforge.services import _audit, _library
from cteforge.services.cards import (
    ProposalOutcome,
    ProposalView,
    card_from_definition,
    json_safe,
    proposal_view,
)
from cteforge.services.context import AppContext, team_mode
from cteforge.services.import_query import import_query
from cteforge.templating.render import render
from cteforge.trust.approval import can_auto_apply
from cteforge.warehouse.base import Warehouse

_IDENT = TypeAdapter(Ident)

Applied = Definition | Recipe | JoinEdge

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
    _audit.record(
        ctx,
        user,
        kind,
        definitions=definitions,
        extra={
            "op": op,
            "proposal_id": proposal.id,
            "proposal_kind": proposal.kind.value,
            "target_id": proposal.target_id,
            "status": proposal.status.value,
            "auto_applied": proposal.auto_applied,
        },
    )


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
    owns the definition; approvers and admins may set any owner (ADR 0018)."""
    if proposed == current or role_at_least(user.role, UserRole.APPROVER):
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
    Raises AnnotationError / SqlParseError when no template can be built."""
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


def logic_changed(old: Definition, new: Definition) -> bool:
    """DESIGN §15: SQL, variables, grain, outputs or join keys changed."""
    keys = ("source_dialect", "template", "variables", "grain", "outputs", "join_keys", "tables")
    return any(getattr(old, k) != getattr(new, k) for k in keys)


# ── Recipe checks ────────────────────────────────────────────────────────────────────


def _recipe_issue(message: str, code: IssueCode = IssueCode.RECIPE_INVALID, **kw: Any) -> Issue:
    return Issue(code=code, severity=Severity.ERROR, message=message, **kw)


def recipe_checks(ctx: AppContext, recipe: Recipe) -> list[Issue]:
    """Validate a recipe without running it: every alias is lower_snake_case and resolves
    to an existing definition (version), values validate and render (templating), the
    final SELECT is one read-only query, and compose accepts the whole (DESIGN §17)."""
    issues: list[Issue] = []
    parts: list[Part] = []
    for alias in recipe.values:
        if alias not in recipe.uses:
            issues.append(
                _recipe_issue(
                    f"values are given for '{alias}', which is not an alias in uses "
                    f"({sorted(recipe.uses)}).",
                    IssueCode.UNKNOWN_ALIAS,
                )
            )
    for alias, ref in recipe.uses.items():
        try:
            _IDENT.validate_python(alias)
        except ValidationError:
            issues.append(
                _recipe_issue(
                    f"Alias '{alias}' must be lower_snake_case (letters, digits, single "
                    "underscores).",
                    IssueCode.UNKNOWN_ALIAS,
                )
            )
            continue
        definition = ctx.store.get_definition(ref.id, ref.version)
        if definition is None:
            at = f" version {ref.version}" if ref.version is not None else ""
            issues.append(
                _recipe_issue(
                    f"'{alias}': definition '{ref.id}'{at} does not exist.", IssueCode.NOT_FOUND
                )
            )
            continue
        if definition.state is DefinitionState.DRAFT:
            issues.append(
                Issue(
                    code=IssueCode.DRAFT_NOT_USABLE,
                    severity=Severity.WARNING,
                    message=f"'{alias}': definition '{ref.id}' is a draft; the recipe cannot "
                    "run until it passes its checks.",
                )
            )
        try:
            rendered = render(definition, recipe.values.get(alias, {}), recipe.dialect)
        except (RenderError, VariableValueError) as exc:
            issues.append(_recipe_issue(f"'{alias}': {exc}"))
            continue
        issues += rendered.issues
        parts.append(Part(alias=alias, definition=definition, sql=rendered.sql))
    if recipe.select is not None and recipe.select.strip():
        try:
            ensure_query(parse(recipe.select, recipe.dialect), "final SELECT")
        except SqlParseError as exc:
            return [*issues, _recipe_issue(str(exc))]
        except RenderError as exc:
            return [*issues, _recipe_issue(str(exc), IssueCode.NOT_READ_ONLY)]
    if has_errors(issues):
        return issues
    try:
        composed = compose(parts, recipe.select, recipe.dialect)
    except (RenderError, SqlParseError) as exc:
        return [*issues, _recipe_issue(str(exc))]
    return [*issues, *composed.issues]


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


def recipe_proposal(ctx: AppContext, user: User, recipe: Recipe, summary: str) -> Proposal:
    """A NEW_RECIPE proposal (next version when the id exists) with its checks."""
    current = ctx.store.get_recipe(recipe.id)
    base = None if current is None else current.version
    proposed = recipe.model_copy(
        update={"version": (base or 0) + 1, "state": DefinitionState.LLM_ANNOTATED}
    )
    return new_proposal(
        ctx,
        user,
        ProposalKind.NEW_RECIPE,
        proposed.id,
        {"recipe": proposed.model_dump(mode="json") | {"values": json_safe(proposed.values)}},
        recipe_checks(ctx, proposed),
        summary=summary,
        base_version=base,
    )


def _join_basis(edge: JoinEdge | None) -> dict[str, Any] | None:
    """What a join proposal is based on: the edge without its usage_count."""
    return None if edge is None else edge.model_dump(mode="json", exclude={"usage_count"})


def _incomplete(message: str) -> Issue:
    return Issue(code=IssueCode.CHECKS_INCOMPLETE, severity=Severity.WARNING, message=message)


def join_checks(ctx: AppContext, edge: JoinEdge) -> list[Issue]:
    """Both tables and every key column exist on the default warehouse profile. Without a
    usable profile the checks are incomplete (the proposal then waits for review)."""
    try:
        _, warehouse = warehouse_for(ctx, None)
    except CteforgeError:
        warehouse = None
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
            issues.append(
                _recipe_issue(
                    f"Table '{table}' does not exist on the warehouse.", IssueCode.NOT_FOUND
                )
            )
            continue
        missing = sorted({c for c in wanted if c.lower() not in columns})
        if missing:
            issues.append(
                _recipe_issue(
                    f"Key column(s) {missing} do not exist in '{table}'.", IssueCode.NOT_FOUND
                )
            )
    return issues


def join_proposal(ctx: AppContext, user: User, edge: JoinEdge, summary: str) -> Proposal:
    """A NEW_JOIN proposal for a canonical edge (an update when the id exists), with its
    checks (`join_checks`) and the edge it is based on (`payload.base_join`)."""
    existing = ctx.store.get_join(edge.id)
    count = 0 if existing is None else existing.usage_count
    edge = edge.model_copy(update={"usage_count": count, "state": DefinitionState.LLM_ANNOTATED})
    return new_proposal(
        ctx,
        user,
        ProposalKind.NEW_JOIN,
        edge.id,
        {"join": edge.model_dump(mode="json"), "base_join": _join_basis(existing)},
        join_checks(ctx, edge),
        summary=summary,
    )


def target_state(ctx: AppContext, proposal: Proposal) -> DefinitionState | None:
    """Current state of what the proposal changes; None when it does not exist yet."""
    if proposal.kind is ProposalKind.NEW_RECIPE:
        recipe = ctx.store.get_recipe(proposal.target_id)
        return None if recipe is None else recipe.state
    if proposal.kind is ProposalKind.NEW_JOIN:
        edge = ctx.store.get_join(proposal.target_id)
        return None if edge is None else edge.state
    definition = ctx.store.get_definition(proposal.target_id)
    return None if definition is None else definition.state


def proposed_state(proposal: Proposal) -> DefinitionState | None:
    """The state a definition proposal's checks earned; None for recipes and joins."""
    if proposal.kind not in (ProposalKind.NEW_DEFINITION, ProposalKind.REVISE_DEFINITION):
        return None
    return definition_from_yaml(str(proposal.payload["definition_yaml"])).state


def may_auto_apply(ctx: AppContext, proposal: Proposal) -> bool:
    """DESIGN §18 via trust.approval.can_auto_apply: open mode, no errors, complete checks,
    target new or not verified (for recipes and joins too), and not a DRAFT definition
    (its checks did not all pass)."""
    state = target_state(ctx, proposal)
    if state is DefinitionState.VERIFIED:
        return False
    if proposed_state(proposal) is DefinitionState.DRAFT:
        return False
    return can_auto_apply(team_mode(ctx), None, proposal.checks)


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


def view(ctx: AppContext, proposal: Proposal) -> ProposalView:
    """Proposal + diff against its base + impact preview (recipes using the definition)."""
    base: Applied | None = None
    impact: list[str] = []
    if proposal.kind in (ProposalKind.NEW_DEFINITION, ProposalKind.REVISE_DEFINITION):
        if proposal.base_version is not None:
            base = ctx.store.get_definition(proposal.target_id, proposal.base_version)
        impact = [r.id for r in ctx.store.recipes_using(proposal.target_id)]
    elif proposal.kind is ProposalKind.NEW_RECIPE:
        if proposal.base_version is not None:
            base = ctx.store.get_recipe(proposal.target_id, proposal.base_version)
    else:
        base = ctx.store.get_join(proposal.target_id)
    return proposal_view(proposal, base, impact)


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
    """Record a decision on a proposal (status, who, when, comment) in the store. Under
    `DECISIONS`, the stored proposal must still be pending (PolicyError otherwise)."""
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
    decided = proposal.model_copy(
        update={
            "status": status,
            "auto_applied": auto,
            "decided_by": user.name,
            "decided_at": ctx.now(),
            "comment": comment,
            "resulting_version": resulting_version,
        }
    )
    return ctx.store.update_proposal(decided)


def _applied_state(proposed: DefinitionState, verify: bool) -> DefinitionState:
    if verify:
        return DefinitionState.VERIFIED
    return DefinitionState.LLM_ANNOTATED if proposed is DefinitionState.VERIFIED else proposed


def _apply_definition(
    ctx: AppContext, proposal: Proposal, user: User, verify: bool
) -> tuple[Definition, int]:
    proposed = definition_from_yaml(str(proposal.payload["definition_yaml"]))
    if verify and proposed.state is DefinitionState.DRAFT:
        raise PolicyError(
            f"Proposal '{proposal.id}' is a draft (its checks did not all pass); it can be "
            "approved as a draft but not verified."
        )
    latest = ctx.store.latest_version(proposal.target_id)
    if latest != proposal.base_version:
        raise stale_error("Definition", proposal.target_id, latest, proposal.base_version)
    version = (latest or 0) + 1
    definition = proposed.model_copy(
        update={
            "version": version,
            "state": _applied_state(proposed.state, verify),
            "verified_by": user.name if verify else None,
            "verified_at": ctx.now().date() if verify else None,
        }
    )
    stored = ctx.store.add_definition_version(
        definition,
        author=proposal.author,
        change_note=proposal.summary,
        logic_changed=bool(proposal.payload.get("logic_changed", True)),
    )
    _library.refresh_join_map(ctx, stored)
    return stored, version


def _apply_recipe(ctx: AppContext, proposal: Proposal, verify: bool) -> tuple[Recipe, int]:
    proposed = Recipe.model_validate(proposal.payload["recipe"])
    current = ctx.store.get_recipe(proposal.target_id)
    latest = None if current is None else current.version
    if latest != proposal.base_version:
        raise stale_error("Recipe", proposal.target_id, latest, proposal.base_version)
    version = (latest or 0) + 1
    state = DefinitionState.VERIFIED if verify else DefinitionState.LLM_ANNOTATED
    recipe = proposed.model_copy(update={"version": version, "state": state})
    return ctx.store.add_recipe_version(recipe, author=proposal.author), version


def _apply_join(ctx: AppContext, proposal: Proposal, verify: bool) -> JoinEdge:
    proposed = JoinEdge.model_validate(proposal.payload["join"])
    # Proposals stored before base_join existed carry no basis and are not checked.
    if "base_join" in proposal.payload:
        current = _join_basis(ctx.store.get_join(proposed.id))
        if current != proposal.payload["base_join"]:
            raise stale_join_error(proposed.id)
    state = DefinitionState.VERIFIED if verify else DefinitionState.LLM_ANNOTATED
    ctx.store.upsert_join(proposed.model_copy(update={"state": state}))
    _library.refresh_join_map(ctx)
    edge = ctx.store.get_join(proposed.id)
    assert edge is not None
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
    """Apply a pending proposal and mark it approved by `user`. Callers check permissions
    and that verify is allowed. Raises StaleVersionError (with a stale_base_version issue)
    when the target moved on since the proposal was made."""
    if proposal.status is not ProposalStatus.PENDING:
        raise PolicyError(f"Proposal '{proposal.id}' is {proposal.status}, not pending")
    with DECISIONS:
        still_pending(ctx, proposal.id)
        version: int | None = None
        applied: Applied
        if proposal.kind in (ProposalKind.NEW_DEFINITION, ProposalKind.REVISE_DEFINITION):
            applied, version = _apply_definition(ctx, proposal, user, verify)
        elif proposal.kind is ProposalKind.NEW_RECIPE:
            applied, version = _apply_recipe(ctx, proposal, verify)
        else:
            applied = _apply_join(ctx, proposal, verify)
        decided = _decide(ctx, proposal, user, ProposalStatus.APPROVED, comment, auto, version)
    return decided, applied
