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

from cteforge.domain.access import Tool, ToolMode
from cteforge.domain.errors import PolicyError

MAX_DISPLAY_NAME = 80
MAX_EMAIL = 254


class UserRole(StrEnum):
    """The built-in role names (ADR 0018). Custom roles have other names (ADR 0027)."""

    VIEWER = "viewer"  # search, describe, render, run
    CONTRIBUTOR = "contributor"  # + propose definitions and joins
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
    # Optional profile (ADR 0029): shown in the GUI's user menu, Users, token lists and
    # the Log's actor; never in search results or definition cards.
    display_name: str | None = None
    email: str | None = None
    # Effective access of the acting user (services/auth); None = the built-in role's.
    tools: frozenset[Tool] | None = None
    review_all: bool | None = None
    # The token this call authenticated with (services/auth); None for the CLI, auth-off
    # HTTP and stdio without CTEFORGE_TOKEN. Bounds what that token may mint (ADR 0027).
    token_id: str | None = None
    token_expires_at: dt.datetime | None = None
    token_mode: ToolMode | None = None
    token_prefix: str | None = None  # Its 12-char display prefix, for the audit log.

    @property
    def is_agent(self) -> bool:
        return self.kind is ActorKind.AGENT


def clean_display_name(value: str | None) -> str | None:
    """A display name as stored: stripped; empty / None clears it. At most
    MAX_DISPLAY_NAME characters, no control characters (PolicyError)."""
    if value is None or not value.strip():
        return None
    text = value.strip()
    if len(text) > MAX_DISPLAY_NAME:
        raise PolicyError(f"A display name has at most {MAX_DISPLAY_NAME} characters")
    if any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise PolicyError("A display name cannot contain control characters")
    return text


def clean_email(value: str | None) -> str | None:
    """An email address as stored: stripped; empty / None clears it. A light format check
    (PolicyError): at most MAX_EMAIL characters, no spaces or control characters, one
    `@` with a non-empty local part and a domain containing a dot (not at either end)."""
    if value is None or not value.strip():
        return None
    text = value.strip()
    if len(text) > MAX_EMAIL:
        raise PolicyError(f"An email address has at most {MAX_EMAIL} characters")
    local, at, domain = text.partition("@")
    valid = (
        bool(at)
        and bool(local)
        and "@" not in domain
        and "." in domain
        and not domain.startswith(".")
        and not domain.endswith(".")
        and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in text)
    )
    if not valid:
        raise PolicyError(f"'{text}' is not an email address (expected name@example.com)")
    return text
