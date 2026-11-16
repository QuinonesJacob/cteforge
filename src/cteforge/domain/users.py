"""Users, roles and actors (ADR 0018). Tokens are never stored here — only their hashes,
and only in store/."""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class UserRole(StrEnum):
    """Ordered from least to most privileged."""

    VIEWER = "viewer"  # search, describe, render, run
    CONTRIBUTOR = "contributor"  # + propose definitions/recipes/joins
    APPROVER = "approver"  # + approve/reject proposals, verify definitions
    ADMIN = "admin"  # + users and settings


ROLE_ORDER = [UserRole.VIEWER, UserRole.CONTRIBUTOR, UserRole.APPROVER, UserRole.ADMIN]


def role_at_least(role: UserRole, minimum: UserRole) -> bool:
    return ROLE_ORDER.index(role) >= ROLE_ORDER.index(minimum)


class ActorKind(StrEnum):
    HUMAN = "human"
    AGENT = "agent"  # Agents can never verify (ADR 0007), whatever their role.


class User(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str  # Unique login name, e.g. "jq" or "claude-agent".
    role: UserRole
    kind: ActorKind = ActorKind.HUMAN
    created_at: dt.datetime | None = None
