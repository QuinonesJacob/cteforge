"""Use-cases: propose a new definition, revise an existing one, rename one (DESIGN §6, §7,
§15, §18; ADR 0024).

Both run the checks engine (services/import_query.py) against a warehouse profile, store a
Proposal with the resulting issues, and auto-apply it when the team is in open mode, every
check passed and was complete, and the target is new or not verified (trust.approval).
Everything else waits for review (services/review.py).

Stale base version: `revise_definition` RAISES StaleVersionError when `base_version` is not
the latest version; the error carries the `stale_base_version` Issue on `.issues`. The same
check runs again when the proposal is applied.

Rename (`rename_definition`, or `revise_definition(new_id=...)` for MCP): a
RENAME_DEFINITION proposal under the same auto-apply rule (open mode, target not verified);
the new id must be a valid id, not exist, and not be another definition's former id
(`_library.check_id_available`). It snapshots the references (`_library.
definition_references`) for the reviewer. Applying it is `_apply.apply_proposal`.

Owner rule: `owner` may be set only to the proposer, or left empty, unless the user is an
approver or admin. A human proposer owns a new definition by default; an agent's has none.

Composed definitions (ADR 0026): `propose_definition` / `revise_definition` take EITHER
`sql` OR `composition` ({uses: alias → {id, version?}, select?, fixed?}; never both). A
composition needs no annotations for its parts (their variables become `<alias>.<var>`);
`annotations` may annotate literals of its final SELECT. Checks: services/_compose.py
`checked_composition`. Revising only the annotations of a composed definition re-checks it
with its current parts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from cteforge.domain.access import Tool
from cteforge.domain.audit import AuditKind
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import CodecError, PolicyError
from cteforge.domain.issues import Issue
from cteforge.domain.models import (
    Annotation,
    Composition,
    Definition,
    DefinitionRef,
    DefinitionState,
    Ident,
)
from cteforge.domain.proposals import ProposalKind
from cteforge.domain.users import ActorKind, User
from cteforge.services import _apply, _audit, _library
from cteforge.services.auth import require
from cteforge.services.cards import ProposalOutcome
from cteforge.services.context import AppContext

_OP_NEW = "propose_definition"
_OP_REVISE = "revise_definition"
_OP_RENAME = "rename_definition"
_ID = TypeAdapter(Ident)


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


class CompositionInput(BaseModel):
    """The `composition` argument (ADR 0026): parts by alias, the final SELECT over the
    aliases (optional with exactly one part), and values the definition always sets on
    its parts (`fixed`: alias → variable → value)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    uses: dict[str, DefinitionRef]
    select: str | None = None
    fixed: dict[str, dict[str, Any]] = {}


def _composition(
    raw: Mapping[str, Any] | CompositionInput,
) -> tuple[Composition, str | None]:
    """(Composition, final SELECT) from the caller's input; CodecError when invalid."""
    try:
        given = (
            raw if isinstance(raw, CompositionInput) else CompositionInput.model_validate(dict(raw))
        )
        return Composition(uses=given.uses, fixed=given.fixed), given.select
    except ValidationError as exc:
        raise CodecError(f"Invalid composition: {_apply.validation_detail(exc)}") from None


def _one_body(sql: str | None, composition: object | None) -> None:
    if (sql is None) == (composition is None):
        raise PolicyError(
            "Give exactly one of `sql` (a trusted query) or `composition` (parts + final "
            "SELECT), not both."
        )


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
    sql: str | None = None,
    composition: Mapping[str, Any] | CompositionInput | None = None,
    dialect: Dialect | str | None = None,
    metadata: Mapping[str, Any] | DefinitionMetadata,
    annotations: Sequence[Annotation | Mapping[str, Any]] = (),
    profile: str | None = None,
    summary: str = "",
) -> ProposalOutcome:
    """Propose a new definition from a trusted query + annotations, or from a composition
    of other definitions (exactly one of `sql` / `composition`; ADR 0026).

    metadata: id, title (required), meaning, keywords, use_when, dont_use_when, grain,
    join_keys, owner, profile. The warehouse profile is `profile`, else metadata.profile,
    else the default one; the definition records the profile its checks ran on.
    An omitted `dialect` is that profile's dialect (the connection's; ADR 0021,
    `_library.resolve_dialect`; PolicyError when there is no profile to take it from).
    Raises StoreError (StaleVersionError) when the id exists — use revise_definition.
    """
    with _audit.audit_failures(ctx, user, AuditKind.PROPOSE, extra={"op": _OP_NEW}):
        require(user, Tool.PROPOSE_DEFINITION)
        _one_body(sql, composition)
        meta = _metadata(metadata)
        if meta.id is None or not (meta.title or "").strip():
            raise CodecError("metadata needs at least 'id' and 'title'")
        latest = ctx.store.latest_version(meta.id)
        if latest is not None:
            raise _apply.stale_error("Definition", meta.id, latest, None)
        _library.check_id_available(ctx, meta.id)  # not a former id (ADR 0024)
        default_owner = user.name if user.kind is ActorKind.HUMAN else None
        owner = meta.owner if "owner" in meta.model_fields_set else default_owner
        _apply.check_owner(user, owner, None)
        source_dialect = _library.resolve_dialect(ctx, dialect, profile or meta.profile)
        profile_name, warehouse = _apply.warehouse_for(ctx, profile or meta.profile)
        fields = meta.given() | {"owner": owner, "profile": profile_name, "version": 1}
        if composition is not None:
            parts, select = _composition(composition)
            definition, issues = _apply.checked_composition(
                ctx,
                user,
                parts,
                select,
                source_dialect,
                _apply.parse_annotations(annotations),
                fields,
                warehouse,
            )
        else:
            assert sql is not None
            definition, issues = _apply.checked_definition(
                sql,
                source_dialect,
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
    composition: Mapping[str, Any] | CompositionInput | None = None,
    dialect: Dialect | str | None = None,
    metadata: Mapping[str, Any] | DefinitionMetadata | None = None,
    annotations: Sequence[Annotation | Mapping[str, Any]] | None = None,
    summary: str = "",
    new_id: str | None = None,
) -> ProposalOutcome:
    """Propose version base_version+1 of a definition — or, with `new_id` (and nothing
    else to change), propose renaming it (`rename_definition`; ADR 0024).

    - `sql` given: new SQL (in `dialect`, default the source dialect) with `annotations`
      (default: the current ones, whose literal IDs must still exist). Checks re-run.
    - `composition` given (never with `sql`): new parts / final SELECT / fixed values
      (ADR 0026), with `annotations` of the final SELECT (default: the current ones when
      the definition is composed). Checks re-run.
    - only `annotations`: the trusted SQL is reconstructed by rendering the current version
      with its original values in its source dialect, then the checks re-run.
    - only `metadata`: template and variables are kept, nothing re-runs; the state is kept
      (a verified definition's revision is proposed as llm_annotated: only a person verifies).
    Revisions of a VERIFIED definition are never auto-applied (ADR 0007).
    Raises NotFoundError, StaleVersionError (with a stale_base_version issue on `.issues`).
    """
    if new_id is not None:
        if (
            sql is not None
            or composition is not None
            or annotations is not None
            or metadata
            or dialect is not None
        ):
            raise PolicyError(
                "A rename is its own proposal: pass only new_id (and summary), then revise "
                "the definition under its new ID."
            )
        return rename_definition(
            ctx, user, id=id, new_id=new_id, base_version=base_version, summary=summary
        )
    with _audit.audit_failures(
        ctx, user, AuditKind.PROPOSE, definitions=[f"{id}@{base_version}"], extra={"op": _OP_REVISE}
    ):
        require(user, Tool.REVISE_DEFINITION)
        current = ctx.store.get_definition(id)
        if current is None:
            raise _library.definition_not_found(ctx, id, hint="; use propose_definition")
        if current.version != base_version:
            raise _apply.stale_error("Definition", id, current.version, base_version)
        meta = _metadata(metadata)
        if meta.id is not None and meta.id != id:
            raise PolicyError(
                "metadata.id can't change the ID; propose a rename instead (new_id, ADR 0024)"
            )
        if sql is not None and composition is not None:
            _one_body(sql, composition)
        if sql is None and composition is None and annotations is None and not meta.given():
            raise PolicyError("Nothing to revise: pass sql, composition, annotations or metadata")
        new_dialect = None if dialect is None else _library.parse_dialect(dialect)
        if sql is None and new_dialect is not None and new_dialect is not current.source_dialect:
            raise PolicyError("Changing the dialect needs the SQL in that dialect (pass sql)")
        given = meta.given()
        _apply.check_owner(user, given.get("owner", current.owner), current.owner)
        merged = current.model_dump() | given | {"version": base_version + 1}
        if sql is None and composition is None and annotations is None:
            revised, issues = _metadata_only(current, merged)
        elif composition is not None or (sql is None and current.composition is not None):
            if composition is not None:
                parts, select = _composition(composition)
            else:
                assert current.composition is not None
                parts = current.composition
                select = _library.original_sql(current) or None
            keep_annotations = annotations is None and current.composition is not None
            anns = (
                _apply.parse_annotations(annotations)
                if annotations is not None
                else (_library.annotations_of(current) if keep_annotations else [])
            )
            profile_name, warehouse = _apply.warehouse_for(ctx, merged.get("profile"))
            merged["profile"] = profile_name
            revised, issues = _apply.checked_composition(
                ctx,
                user,
                parts,
                select,
                new_dialect or current.source_dialect,
                anns,
                merged,
                warehouse,
            )
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


def rename_definition(
    ctx: AppContext,
    user: User,
    *,
    id: str,
    new_id: str,
    base_version: int | None = None,
    summary: str = "",
) -> ProposalOutcome:
    """Propose renaming definition `id` to `new_id` (ADR 0024). Same policy as any change:
    auto-applied in open mode when the definition is not verified, else it waits for a
    person (a verified one for a human; four-eyes). `base_version`, when given, must be the
    current version (a guard against renaming what you have not seen). The proposal keeps
    the references seen now. Raises NotFoundError (a former id names the current one),
    CodecError (not a valid id), PolicyError (same id, taken, or a former id of another
    definition), StaleVersionError."""
    fields = {"definitions": [id], "extra": {"op": _OP_RENAME, "new_id": new_id}}
    with _audit.audit_failures(ctx, user, AuditKind.PROPOSE, **fields):
        require(user, Tool.REVISE_DEFINITION)
        current = ctx.store.get_definition(id)
        if current is None:
            raise _library.definition_not_found(ctx, id)
        if base_version is not None and current.version != base_version:
            raise _apply.stale_error("Definition", id, current.version, base_version)
        try:
            new_id = _ID.validate_python(new_id.strip())
        except ValidationError:
            raise CodecError(
                f"'{new_id}' is not a valid ID: lower_snake_case (a letter first, then "
                "letters, digits and single underscores)"
            ) from None
        if new_id == id:
            raise PolicyError(f"'{id}' is already the definition's ID; nothing to rename")
        _library.check_id_available(ctx, new_id, renaming=id)
        proposal = _apply.rename_proposal(ctx, user, current, new_id, summary)
        result = _apply.submit(ctx, user, proposal)
    _apply.record_proposal(ctx, user, AuditKind.PROPOSE, result.proposal, _OP_RENAME)
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
