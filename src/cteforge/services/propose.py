"""Use-cases: propose a new definition, revise an existing one (DESIGN §6, §7, §15, §18).

Both run the checks engine (services/import_query.py) against a warehouse profile, store a
Proposal with the resulting issues, and auto-apply it when the team is in open mode, every
check passed and was complete, and the target is new or not verified (trust.approval).
Everything else waits for review (services/review.py).

Stale base version: `revise_definition` RAISES StaleVersionError when `base_version` is not
the latest version; the error carries the `stale_base_version` Issue on `.issues`. The same
check runs again when the proposal is applied.

Owner rule: `owner` may be set only to the proposer, or left empty, unless the user is an
approver or admin. A human proposer owns a new definition by default; an agent's has none.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from cteforge.domain.audit import AuditKind
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import CodecError, NotFoundError, PolicyError
from cteforge.domain.issues import Issue
from cteforge.domain.models import Annotation, Definition, DefinitionState, Ident
from cteforge.domain.proposals import ProposalKind
from cteforge.domain.users import ActorKind, User
from cteforge.services import _apply, _audit, _library
from cteforge.services.auth import require
from cteforge.services.cards import ProposalOutcome
from cteforge.services.context import AppContext
from cteforge.trust.approval import Action

_OP_NEW = "propose_definition"
_OP_REVISE = "revise_definition"


class DefinitionMetadata(BaseModel):
    """The `metadata` argument. Keys not given are left as they are (revise) or defaulted."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: Ident | None = None
    title: str | None = None
    meaning: str | None = None
    keywords: list[str] | None = None
    use_when: str | None = None
    dont_use_when: str | None = None
    grain: str | None = None
    join_keys: list[str] | None = None
    owner: str | None = None
    profile: str | None = None

    def given(self) -> dict[str, Any]:
        """Only the keys the caller set (an explicit null counts: it clears the field)."""
        return {k: getattr(self, k) for k in self.model_fields_set}


def _metadata(raw: Mapping[str, Any] | DefinitionMetadata | None) -> DefinitionMetadata:
    if raw is None:
        return DefinitionMetadata()
    if isinstance(raw, DefinitionMetadata):
        return raw
    try:
        return DefinitionMetadata.model_validate(dict(raw))
    except ValidationError as exc:
        raise CodecError(f"Invalid metadata: {_apply.validation_detail(exc)}") from None


def propose_definition(
    ctx: AppContext,
    user: User,
    *,
    sql: str,
    dialect: Dialect | str,
    metadata: Mapping[str, Any] | DefinitionMetadata,
    annotations: Sequence[Annotation | Mapping[str, Any]],
    profile: str | None = None,
    summary: str = "",
) -> ProposalOutcome:
    """Propose a new definition from a trusted query + annotations.

    metadata: id, title (required), meaning, keywords, use_when, dont_use_when, grain,
    join_keys, owner, profile. The warehouse profile is `profile`, else metadata.profile,
    else the default one; the definition records the profile its checks ran on.
    Raises StoreError (StaleVersionError) when the id exists — use revise_definition.
    """
    with _audit.audit_failures(ctx, user, AuditKind.PROPOSE, extra={"op": _OP_NEW}):
        require(user, Action.PROPOSE)
        meta = _metadata(metadata)
        if meta.id is None or not (meta.title or "").strip():
            raise CodecError("metadata needs at least 'id' and 'title'")
        latest = ctx.store.latest_version(meta.id)
        if latest is not None:
            raise _apply.stale_error("Definition", meta.id, latest, None)
        default_owner = user.name if user.kind is ActorKind.HUMAN else None
        owner = meta.owner if "owner" in meta.model_fields_set else default_owner
        _apply.check_owner(user, owner, None)
        profile_name, warehouse = _apply.warehouse_for(ctx, profile or meta.profile)
        fields = meta.given() | {"owner": owner, "profile": profile_name, "version": 1}
        definition, issues = _apply.checked_definition(
            sql,
            _library.parse_dialect(dialect),
            _apply.parse_annotations(annotations),
            fields,
            warehouse,
        )
        proposal = _apply.new_proposal(
            ctx,
            user,
            ProposalKind.NEW_DEFINITION,
            definition.id,
            _apply.definition_payload(definition, changed_logic=True),
            issues,
            summary=summary,
        )
        result = _apply.submit(ctx, user, proposal)
    _apply.record_proposal(ctx, user, AuditKind.PROPOSE, result.proposal, _OP_NEW)
    return result


def revise_definition(
    ctx: AppContext,
    user: User,
    *,
    id: str,
    base_version: int,
    sql: str | None = None,
    dialect: Dialect | str | None = None,
    metadata: Mapping[str, Any] | DefinitionMetadata | None = None,
    annotations: Sequence[Annotation | Mapping[str, Any]] | None = None,
    summary: str = "",
) -> ProposalOutcome:
    """Propose version base_version+1 of a definition.

    - `sql` given: new SQL (in `dialect`, default the source dialect) with `annotations`
      (default: the current ones, whose literal IDs must still exist). Checks re-run.
    - only `annotations`: the trusted SQL is reconstructed by rendering the current version
      with its original values in its source dialect, then the checks re-run.
    - only `metadata`: template and variables are kept, nothing re-runs; the state is kept
      (a verified definition's revision is proposed as llm_annotated: only a person verifies).
    Revisions of a VERIFIED definition are never auto-applied (ADR 0007).
    Raises NotFoundError, StaleVersionError (with a stale_base_version issue on `.issues`).
    """
    with _audit.audit_failures(
        ctx, user, AuditKind.PROPOSE, definitions=[f"{id}@{base_version}"], extra={"op": _OP_REVISE}
    ):
        require(user, Action.PROPOSE)
        current = ctx.store.get_definition(id)
        if current is None:
            raise NotFoundError(f"Definition '{id}' does not exist; use propose_definition")
        if current.version != base_version:
            raise _apply.stale_error("Definition", id, current.version, base_version)
        meta = _metadata(metadata)
        if meta.id is not None and meta.id != id:
            raise PolicyError("A definition's id cannot change; propose a new definition")
        if sql is None and annotations is None and not meta.given():
            raise PolicyError("Nothing to revise: pass sql, annotations or metadata")
        new_dialect = None if dialect is None else _library.parse_dialect(dialect)
        if sql is None and new_dialect is not None and new_dialect is not current.source_dialect:
            raise PolicyError("Changing the dialect needs the SQL in that dialect (pass sql)")
        given = meta.given()
        _apply.check_owner(user, given.get("owner", current.owner), current.owner)
        merged = current.model_dump() | given | {"version": base_version + 1}
        if sql is None and annotations is None:
            revised, issues = _metadata_only(current, merged)
        else:
            source_sql = sql if sql is not None else _library.original_sql(current)
            source_dialect = new_dialect or current.source_dialect
            anns = (
                _apply.parse_annotations(annotations)
                if annotations is not None
                else _library.annotations_of(current)
            )
            profile_name, warehouse = _apply.warehouse_for(ctx, merged.get("profile"))
            merged["profile"] = profile_name
            revised, issues = _apply.checked_definition(
                source_sql, source_dialect, anns, merged, warehouse
            )
        proposal = _apply.new_proposal(
            ctx,
            user,
            ProposalKind.REVISE_DEFINITION,
            id,
            _apply.definition_payload(
                revised, changed_logic=_apply.logic_changed(current, revised)
            ),
            issues,
            summary=summary,
            base_version=base_version,
        )
        result = _apply.submit(ctx, user, proposal)
    _apply.record_proposal(ctx, user, AuditKind.PROPOSE, result.proposal, _OP_REVISE)
    return result


def _metadata_only(current: Definition, merged: dict[str, Any]) -> tuple[Definition, list[Issue]]:
    state = current.state
    if state is DefinitionState.VERIFIED:
        state = DefinitionState.LLM_ANNOTATED
    merged |= {"state": state, "verified_by": None, "verified_at": None}
    try:
        revised = Definition.model_validate(merged)
    except ValidationError as exc:
        raise CodecError(f"Invalid metadata: {_apply.validation_detail(exc)}") from None
    _apply.check_join_keys(revised)
    return revised, []
