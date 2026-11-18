"""Proposals: every change to the library goes through one (DESIGN §18)."""

from __future__ import annotations

import datetime as dt
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from cteforge.domain.codec import definition_from_yaml, definition_to_yaml
from cteforge.domain.issues import Issue
from cteforge.domain.users import ActorKind


class ProposalKind(StrEnum):
    NEW_DEFINITION = "new_definition"
    REVISE_DEFINITION = "revise_definition"
    # Legacy (ADR 0026 removed recipes): only proposals stored before then carry it; the
    # store migration withdrew the pending ones. Nothing creates it any more.
    NEW_RECIPE = "new_recipe"
    NEW_JOIN = "new_join"  # add an edge, or annotate one (payload.action; ADR 0026)
    RENAME_DEFINITION = "rename_definition"  # A definition's ID changes (ADR 0024).


# Kinds whose target_id is a definition id.
DEFINITION_KINDS = frozenset(
    {
        ProposalKind.NEW_DEFINITION,
        ProposalKind.REVISE_DEFINITION,
        ProposalKind.RENAME_DEFINITION,
    }
)


class ProposalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"  # Applied to the library (possibly auto-applied in open mode).
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str  # uuid4 hex
    kind: ProposalKind
    target_id: str  # Definition / join id the proposal creates or changes.
    base_version: int | None = None  # revise_definition: version it was based on.
    # Payload by kind: definitions → {"definition_yaml": str, "logic_changed": bool};
    # joins → {"join": dict, "base_join": dict | None, "action": "add" | "annotate",
    # "evidence": RelationshipEvidence dict | None}; renames → {"from_id", "to_id",
    # "references": dict (the DefinitionReferences seen when proposed)}; legacy recipes →
    # {"recipe": dict}. Always plain JSON-compatible data.
    payload: dict[str, Any]
    checks: list[Issue] = Field(default_factory=list)
    summary: str = ""  # Author's one-line description of the change.
    author: str
    author_kind: ActorKind
    created_at: dt.datetime
    status: ProposalStatus = ProposalStatus.PENDING
    auto_applied: bool = False
    decided_by: str | None = None
    decided_by_kind: ActorKind | None = None  # ADR 0027: cards show agent decisions.
    decided_at: dt.datetime | None = None
    comment: str = ""
    resulting_version: int | None = None


def proposal_with_definition_renamed(p: Proposal, old: str, new: str) -> Proposal | None:
    """`p` with every stored copy of definition id `old` replaced by `new` (ADR 0024), or
    None when it names `old` nowhere: a definition proposal's `target_id`, the id in its
    definition YAML and the parts its composition uses (ADR 0026); the uses of a legacy
    recipe proposal. A rename proposal's `from_id` / `to_id` are the record of what it
    asked for and are kept (only its target moves)."""
    update: dict[str, Any] = {}
    payload = dict(p.payload)
    if p.kind in DEFINITION_KINDS and p.target_id == old:
        update["target_id"] = new
    text = payload.get("definition_yaml")
    if p.kind in DEFINITION_KINDS and isinstance(text, str):
        definition = definition_from_yaml(text)
        renamed = definition.with_definition_renamed(old, new)
        if renamed is not definition:
            payload["definition_yaml"] = definition_to_yaml(renamed)
            update["payload"] = payload
    recipe = payload.get("recipe")
    if p.kind is ProposalKind.NEW_RECIPE and isinstance(recipe, dict):
        uses = recipe.get("uses")
        if isinstance(uses, dict) and any(
            isinstance(ref, dict) and ref.get("id") == old for ref in uses.values()
        ):
            renamed_uses = {
                alias: (ref | {"id": new})
                if isinstance(ref, dict) and ref.get("id") == old
                else ref
                for alias, ref in uses.items()
            }
            payload["recipe"] = recipe | {"uses": renamed_uses}
            update["payload"] = payload
    return p.model_copy(update=update) if update else None
