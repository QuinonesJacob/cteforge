"""Use-cases: propose a join-map edge or a recipe (DESIGN §3, §17, §18).

Same auto-apply rule as definitions: open mode, no error issue, complete checks, and the
target (join edge / recipe id) new or not verified. Otherwise the proposal waits for review.

- Joins: the edge is normalized (alphabetically first table on the left, keys lowercased
  and sorted) and gets the deterministic id `_library.join_edge_id`, so proposing a mined
  edge again updates it (relationship, traps, join type). `usage_count` is never taken
  from the caller; it is recomputed when the proposal is applied.
- Recipes: validated without running anything (`_apply.recipe_checks`): every use
  resolves to an existing definition, values validate and render, the final SELECT is one
  read-only query, compose accepts it. Proposing an existing recipe id proposes its next
  version (`base_version` = the latest, re-checked when applied). Callers give only a
  `RecipeInput` (id, title, description, uses, values, select, dialect): `version`,
  `state` and `schema_version` are set by cteforge, and a mapping that sets them is
  refused with CodecError instead of being silently ignored.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cteforge.domain.audit import AuditKind
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import CodecError
from cteforge.domain.models import DefinitionRef, DefinitionState, Ident, JoinKey, Recipe
from cteforge.domain.users import User
from cteforge.services import _apply, _audit, _library
from cteforge.services.auth import require
from cteforge.services.cards import ProposalOutcome
from cteforge.services.context import AppContext
from cteforge.trust.approval import Action

KeyInput = JoinKey | Mapping[str, str] | Sequence[str]


def _keys(keys: Sequence[KeyInput]) -> list[JoinKey]:
    out: list[JoinKey] = []
    for key in keys:
        if isinstance(key, JoinKey):
            out.append(key)
        elif isinstance(key, Mapping):
            try:
                out.append(JoinKey.model_validate(dict(key)))
            except ValidationError:
                raise CodecError("Each join key needs 'left' and 'right' column names") from None
        elif isinstance(key, Sequence) and not isinstance(key, str) and len(key) == 2:
            out.append(JoinKey(left=str(key[0]), right=str(key[1])))
        else:
            raise CodecError("Each join key is {left, right} or a [left, right] pair")
    if not out or any(not k.left.strip() or not k.right.strip() for k in out):
        raise CodecError("A join needs at least one key pair of non-empty column names")
    return out


def propose_join(
    ctx: AppContext,
    user: User,
    *,
    left_table: str,
    right_table: str,
    keys: Sequence[KeyInput],
    join_type: str = "inner",
    relationship: str | None = None,
    traps: Sequence[str] = (),
    summary: str = "",
) -> ProposalOutcome:
    """Propose a join-map edge (new, or an update of an existing edge with the same id)."""
    with _audit.audit_failures(ctx, user, AuditKind.PROPOSE, extra={"op": "propose_join"}):
        require(user, Action.PROPOSE)
        if not left_table.strip() or not right_table.strip():
            raise CodecError("A join needs both table names")
        if join_type.strip().lower() not in _library.JOIN_TYPES:
            raise CodecError(f"join_type must be one of {list(_library.JOIN_TYPES)}")
        if relationship is not None and relationship not in _library.RELATIONSHIPS:
            raise CodecError(f"relationship must be one of {list(_library.RELATIONSHIPS)}")
        edge = _library.canonical_edge(
            left_table,
            right_table,
            _keys(keys),
            join_type,
            relationship=relationship,
            traps=[t.strip() for t in traps if t.strip()],
            state=DefinitionState.LLM_ANNOTATED,
        )
        proposal = _apply.join_proposal(ctx, user, edge, summary)
        result = _apply.submit(ctx, user, proposal)
    _apply.record_proposal(ctx, user, AuditKind.PROPOSE, result.proposal, "propose_join")
    return result


# Recipe fields cteforge sets itself; a caller may not.
RESERVED_RECIPE_FIELDS = ("version", "state", "schema_version")


class RecipeInput(BaseModel):
    """A recipe as proposed: definitions by reference, values, optional final SELECT."""

    # The MCP/API input schema. RESERVED_RECIPE_FIELDS are deliberately absent: cteforge
    # sets them when the recipe is applied.

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: Ident
    title: str
    description: str = ""
    uses: dict[str, DefinitionRef]
    values: dict[str, dict[str, Any]] = Field(default_factory=dict)
    select: str | None = None
    dialect: Dialect


def recipe_from(raw: Recipe | RecipeInput | Mapping[str, Any]) -> Recipe:
    if isinstance(raw, Recipe):
        return raw
    if isinstance(raw, Mapping):
        reserved = [k for k in RESERVED_RECIPE_FIELDS if k in raw]
        if reserved:
            raise CodecError(
                f"Invalid recipe: {reserved} cannot be set by a proposal; cteforge sets the "
                "version and state when the recipe is applied. Remove them."
            )
        try:
            raw = RecipeInput.model_validate(dict(raw))
        except ValidationError as exc:
            raise CodecError(f"Invalid recipe: {_apply.validation_detail(exc)}") from None
    try:
        return Recipe.model_validate(raw.model_dump())
    except ValidationError as exc:
        raise CodecError(f"Invalid recipe: {_apply.validation_detail(exc)}") from None


def propose_recipe(
    ctx: AppContext,
    user: User,
    *,
    recipe: Recipe | RecipeInput | Mapping[str, Any],
    summary: str = "",
) -> ProposalOutcome:
    """Propose a recipe (or the next version of one). Its `version` and `state` are set
    when it is applied (a mapping that sets them, or `schema_version`, raises
    CodecError); checks are returned as issues (errors block approval)."""
    with _audit.audit_failures(ctx, user, AuditKind.PROPOSE, extra={"op": "propose_recipe"}):
        require(user, Action.PROPOSE)
        proposal = _apply.recipe_proposal(ctx, user, recipe_from(recipe), summary)
        result = _apply.submit(ctx, user, proposal)
    _apply.record_proposal(ctx, user, AuditKind.PROPOSE, result.proposal, "propose_recipe")
    return result
