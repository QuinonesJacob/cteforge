"""Tool catalog, roles and personal access tokens (ADR 0027). Pure data; no I/O.

The permission unit is the **tool**: one name per operation, the same names MCP uses.
A role's `allowed` list is the ceiling of what its users may ever do; `defaults` is what a
new token starts with. A token's own tool list (`tool_mode`, `include`, `exclude`) narrows
that further. How the three combine (the effective tools) is policy: trust/approval.py.

Never here: token values or their hashes (`TokenInfo` holds only the display prefix).
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from cteforge.domain.models import Ident


class Tool(StrEnum):
    """Every operation a user or token may be allowed (ADR 0027 addendum)."""

    # use
    SEARCH = "search"
    GET_DEFINITION = "get_definition"
    COMPILE_AND_RUN = "compile_and_run"
    CHECK_AND_RUN = "check_and_run"
    DESCRIBE_TABLES = "describe_tables"
    LIST_JOINS = "list_joins"
    LIST_RECIPES = "list_recipes"  # until ADR 0026 removes recipes
    # build
    PARSE_QUERY = "parse_query"
    PROPOSE_DEFINITION = "propose_definition"
    REVISE_DEFINITION = "revise_definition"
    PROPOSE_JOIN = "propose_join"
    PROPOSE_RECIPE = "propose_recipe"  # until ADR 0026
    # review
    LIST_PROPOSALS = "list_proposals"
    REVIEW_PROPOSAL = "review_proposal"  # approve / reject
    VERIFY = "verify"
    # admin
    VIEW_AUDIT = "view_audit"
    LIST_USERS = "list_users"
    MANAGE_USER = "manage_user"
    MANAGE_ROLE = "manage_role"
    MANAGE_SETTINGS = "manage_settings"


class ToolGroup(StrEnum):
    USE = "use"
    BUILD = "build"
    REVIEW = "review"
    ADMIN = "admin"


TOOL_GROUPS: dict[ToolGroup, tuple[Tool, ...]] = {
    ToolGroup.USE: (
        Tool.SEARCH,
        Tool.GET_DEFINITION,
        Tool.COMPILE_AND_RUN,
        Tool.CHECK_AND_RUN,
        Tool.DESCRIBE_TABLES,
        Tool.LIST_JOINS,
        Tool.LIST_RECIPES,
    ),
    ToolGroup.BUILD: (
        Tool.PARSE_QUERY,
        Tool.PROPOSE_DEFINITION,
        Tool.REVISE_DEFINITION,
        Tool.PROPOSE_JOIN,
        Tool.PROPOSE_RECIPE,
    ),
    ToolGroup.REVIEW: (Tool.LIST_PROPOSALS, Tool.REVIEW_PROPOSAL, Tool.VERIFY),
    ToolGroup.ADMIN: (
        Tool.VIEW_AUDIT,
        Tool.LIST_USERS,
        Tool.MANAGE_USER,
        Tool.MANAGE_ROLE,
        Tool.MANAGE_SETTINGS,
    ),
}

ALL_TOOLS: frozenset[Tool] = frozenset(Tool)

# Only the admin role may allow these (ADR 0027 addendum).
ADMIN_ONLY_TOOLS: frozenset[Tool] = frozenset(
    {Tool.LIST_USERS, Tool.MANAGE_USER, Tool.MANAGE_ROLE, Tool.MANAGE_SETTINGS}
)


def tool_group(tool: Tool) -> ToolGroup:
    for group, tools in TOOL_GROUPS.items():
        if tool in tools:
            return group
    raise KeyError(tool)  # Unreachable: every Tool is in exactly one group.


def sorted_tools(tools: frozenset[Tool] | set[Tool] | list[Tool] | tuple[Tool, ...]) -> list[Tool]:
    """Tools in catalog order (the order of `Tool`), without duplicates."""
    wanted = set(tools)
    return [t for t in Tool if t in wanted]


class ToolMode(StrEnum):
    FOLLOW_ROLE = "follow_role"  # role defaults ∪ include − exclude; new defaults follow
    ONLY = "only"  # exactly `include`; new tools stay off


ADMIN_ROLE = "admin"


class Role(BaseModel):
    """A role: the ceiling (`allowed`) and what new tokens start with (`defaults`)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Ident
    description: str = ""
    allowed: frozenset[Tool] = frozenset()
    defaults: frozenset[Tool] = frozenset()
    review_all: bool = False  # Approve/reject anything, not only what the user owns.
    builtin: bool = False

    @field_validator("name")
    @classmethod
    def _short_name(cls, value: str) -> str:
        if len(value) > 32:
            raise ValueError("role names are at most 32 characters")
        return value

    @model_validator(mode="after")
    def _defaults_within_allowed(self) -> Role:
        if not self.defaults <= self.allowed:
            extra = sorted(t.value for t in self.defaults - self.allowed)
            raise ValueError(f"defaults must be allowed too (not allowed: {extra})")
        return self


class TokenInfo(BaseModel):
    """A personal access token's metadata. Never the token or its hash."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    user_name: str
    name: str = Field(min_length=1, max_length=100)
    prefix: str  # First characters of the token, for recognizing it in lists ("" = legacy)
    created_at: dt.datetime
    created_by: str
    last_used_at: dt.datetime | None = None
    expires_at: dt.datetime | None = None
    revoked_at: dt.datetime | None = None
    tool_mode: ToolMode = ToolMode.FOLLOW_ROLE
    include: frozenset[Tool] = frozenset()
    exclude: frozenset[Tool] = frozenset()
    # The token that minted this one for its own user (revoking it revokes this one).
    parent_token_id: str | None = None

    def usable_at(self, now: dt.datetime) -> bool:
        """Not revoked and not expired at `now`."""
        return self.revoked_at is None and (self.expires_at is None or self.expires_at > now)


_USE = frozenset(TOOL_GROUPS[ToolGroup.USE])
_BUILD = frozenset(TOOL_GROUPS[ToolGroup.BUILD])
_VIEWER = _USE | {Tool.LIST_PROPOSALS}
_CONTRIBUTOR = _VIEWER | _BUILD | {Tool.REVIEW_PROPOSAL}
_APPROVER = _CONTRIBUTOR | {Tool.VERIFY, Tool.VIEW_AUDIT}

# The built-in roles, seeded into the store with today's behaviour (ADR 0027 addendum).
# Also the fallback for a User built without resolved tools (tests, before a lookup).
BUILTIN_ROLES: dict[str, Role] = {
    r.name: r
    for r in (
        Role(
            name="viewer",
            description="Search, describe and run the library.",
            allowed=_VIEWER,
            defaults=_VIEWER,
            builtin=True,
        ),
        Role(
            name="contributor",
            description="Viewer, plus propose changes; approve changes to what they own.",
            allowed=_CONTRIBUTOR,
            defaults=_CONTRIBUTOR,
            builtin=True,
        ),
        Role(
            name="approver",
            description="Contributor, plus approve or reject anything, verify, read the audit.",
            allowed=_APPROVER,
            defaults=_APPROVER,
            review_all=True,
            builtin=True,
        ),
        Role(
            name=ADMIN_ROLE,
            description="Everything, including users, roles, tokens and settings.",
            allowed=ALL_TOOLS,
            defaults=ALL_TOOLS,
            review_all=True,
            builtin=True,
        ),
    )
}
