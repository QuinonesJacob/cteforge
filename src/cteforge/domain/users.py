"""Users and actors (ADR 0018, 0027). Tokens are never stored here — only their hashes,
and only in store/.

A user's `role` is the NAME of a role (domain/access.py `Role`; the built-in names are
the `UserRole` values). `tools` / `review_all` are the acting user's effective access,
resolved by services/auth at authentication (role ∩ token, ADR 0027); a User built
without them (tests, before a store lookup) falls back to its built-in role's defaults
(trust/approval.py).
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from cteforge.domain.access import Tool


class UserRole(StrEnum):
    """The built-in role names (ADR 0018). Custom roles have other names (ADR 0027)."""

    VIEWER = "viewer"  # search, describe, render, run
    CONTRIBUTOR = "contributor"  # + propose definitions/recipes/joins
    APPROVER = "approver"  # + approve/reject proposals, verify definitions
    ADMIN = "admin"  # + users, roles, tokens and settings


class ActorKind(StrEnum):
    HUMAN = "human"
    AGENT = "agent"  # Agents verify only with the admin role (ADR 0027).


class User(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str  # Unique login name, e.g. "jq" or "claude-agent".
    role: str  # A role name (UserRole values for the built-ins).
    kind: ActorKind = ActorKind.HUMAN
    active: bool = True  # Deactivated users cannot authenticate (ADR 0027).
    created_at: dt.datetime | None = None
    # Effective access of the acting user (services/auth); None = the built-in role's.
    tools: frozenset[Tool] | None = None
    review_all: bool | None = None

    @property
    def is_agent(self) -> bool:
        return self.kind is ActorKind.AGENT
