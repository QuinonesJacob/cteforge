"""Proposals: every change to the library goes through one (DESIGN §18)."""

from __future__ import annotations

import datetime as dt
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from cteforge.domain.issues import Issue
from cteforge.domain.users import ActorKind


class ProposalKind(StrEnum):
    NEW_DEFINITION = "new_definition"
    REVISE_DEFINITION = "revise_definition"
    NEW_RECIPE = "new_recipe"
    NEW_JOIN = "new_join"


class ProposalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"  # Applied to the library (possibly auto-applied in open mode).
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str  # uuid4 hex
    kind: ProposalKind
    target_id: str  # Definition / recipe / join id the proposal creates or changes.
    base_version: int | None = None  # revise_definition: version it was based on.
    # Payload by kind: definitions → {"definition_yaml": str}; recipes → {"recipe": dict};
    # joins → {"join": dict}. Always plain JSON-compatible data.
    payload: dict[str, Any]
    checks: list[Issue] = Field(default_factory=list)
    summary: str = ""  # Author's one-line description of the change.
    author: str
    author_kind: ActorKind
    created_at: dt.datetime
    status: ProposalStatus = ProposalStatus.PENDING
    auto_applied: bool = False
    decided_by: str | None = None
    decided_at: dt.datetime | None = None
    comment: str = ""
    resulting_version: int | None = None
