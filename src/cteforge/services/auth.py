"""Authentication and permission checks (ADR 0018, 0027).

Tokens are personal access tokens: `cfg_pat_` + 43 random URL-safe characters
(`new_token`), shown once, stored only as SHA-256 hashes (store `tokens`). A failed lookup
— unknown, revoked or expired token, deactivated user, malformed input — raises one
generic PermissionDeniedError whatever the reason, so callers cannot probe which tokens
or users exist.

The acting user carries its effective tools (ADR 0027 addendum), resolved here:
- a token (HTTP API, MCP over HTTP, stdio MCP with CTEFORGE_TOKEN) → the token's tools
  (trust.approval.effective_tools: role ceiling ∩ token list);
- stdio MCP by CTEFORGE_USER → the role's defaults;
- the CLI and auth-off HTTP (a person at the machine) → the role's allowed tools.
Permission policy itself lives in trust/approval.py; `require` is the one check every
use-case makes.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import secrets

from cteforge.domain.access import Tool
from cteforge.domain.audit import AuditKind
from cteforge.domain.errors import PermissionDeniedError, StoreError
from cteforge.domain.models import Definition
from cteforge.domain.users import ActorKind, User, UserRole
from cteforge.services import _audit
from cteforge.services.context import AppContext
from cteforge.trust.approval import (
    allowed,
    can_approve,
    can_verify,
    effective_tools,
    role_tools,
    tools_of,
)

_log = logging.getLogger(__name__)

_INVALID_TOKEN = "Invalid or missing token"
_MAX_TOKEN_LENGTH = 512  # Far above a real token; refuses absurd inputs cheaply.
TOKEN_PREFIX = "cfg_pat_"  # So secret scanners can recognize a leaked token.
DISPLAY_PREFIX_LENGTH = 12  # Characters of a token kept for lists ("cfg_pat_" + 4).
LAST_USED_INTERVAL = dt.timedelta(minutes=1)  # last_used_at is written at most this often.


def hash_token(token: str) -> str:
    """SHA-256 hex digest: the only form in which a token is stored."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> str:
    """A fresh personal access token: `cfg_pat_` + 43 URL-safe chars. Show it once;
    store its hash."""
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def display_prefix(token: str) -> str:
    """What lists show of a token (its first 12 characters)."""
    return token[:DISPLAY_PREFIX_LENGTH]


def _with_role_access(ctx: AppContext, user: User, *, defaults_only: bool) -> User:
    role = ctx.store.get_role(user.role)
    if role is None:  # A role that no longer exists grants nothing.
        return user.model_copy(update={"tools": frozenset(), "review_all": False})
    tools = role_tools(role, user.kind, defaults_only=defaults_only)
    return user.model_copy(update={"tools": tools, "review_all": role.review_all})


def authenticate(ctx: AppContext, token: str | None) -> User:
    """Bearer token → the acting User with the token's effective tools. Any failure
    (missing, malformed, unknown, revoked, expired, deactivated user) raises the same
    PermissionDeniedError with a generic message."""
    if not token or len(token) > _MAX_TOKEN_LENGTH:
        raise PermissionDeniedError(_INVALID_TOKEN)
    now = ctx.now()
    found = ctx.store.user_by_token_hash(hash_token(token), now)
    if found is None:
        raise PermissionDeniedError(_INVALID_TOKEN)
    user, info = found
    role = ctx.store.get_role(user.role)
    if role is None:
        tools: frozenset[Tool] = frozenset()
        review_all = False
    else:
        tools = effective_tools(role, info, user.kind)
        review_all = role.review_all
    if info.last_used_at is None or now - info.last_used_at >= LAST_USED_INTERVAL:
        try:
            ctx.store.touch_token(info.id, now, min_interval=LAST_USED_INTERVAL)
        except StoreError as exc:  # Best effort: never fail a call over bookkeeping.
            _log.warning("Could not record token use (%s)", type(exc).__name__)
    return user.model_copy(update={"tools": tools, "review_all": review_all})


def local_user(ctx: AppContext, *, create: bool = True, defaults_only: bool = False) -> User:
    """The local user named by CTEFORGE_USER, with its role's tools: allowed (CLI,
    auth-off HTTP — a person at the machine) or, with `defaults_only`, the role's defaults
    (stdio MCP without CTEFORGE_TOKEN).

    Only for stdio MCP, the CLI, or HTTP with CTEFORGE_AUTH=off (bound to localhost; the
    API enforces that). An existing user is used as stored (role and kind); a deactivated
    one is refused. A missing user is created as a human admin ONLY when `create` is true
    and the store has no users at all (first local use by a person, like
    `admin.bootstrap_admin`); in a store that already has users, an unknown name raises
    PermissionDeniedError, so a typo or an agent's name never becomes a human admin.
    `create=False` (stdio MCP: the caller is an agent's MCP client, whatever
    CTEFORGE_USER says) never creates anyone: an empty store raises PermissionDeniedError
    naming `cteforge user add --bootstrap`. A user created here has no token, so it
    cannot be used over authenticated HTTP until someone generates one.
    """
    name = ctx.config.user
    user = ctx.store.get_user(name)
    if user is not None:
        if not user.active:
            raise PermissionDeniedError(f"Local user '{name}' is deactivated")
        return _with_role_access(ctx, user, defaults_only=defaults_only)
    if not create:
        raise PermissionDeniedError(
            f"Local user '{name}' does not exist. A person must create users first: "
            "`cteforge user add <name> --bootstrap` for the first admin, then `cteforge "
            f"user add {name} --role contributor --agent` for an agent (CTEFORGE_USER is "
            "never created from here)"
        )
    if ctx.store.list_users():
        raise PermissionDeniedError(
            f"Local user '{name}' does not exist; ask an admin to create it "
            "(CTEFORGE_USER must name an existing user once any user exists)"
        )
    candidate = User(name=name, role=UserRole.ADMIN, kind=ActorKind.HUMAN, created_at=ctx.now())
    try:
        created = ctx.store.add_user(candidate)
    except StoreError:
        # Created concurrently by another call: use that one.
        existing = ctx.store.get_user(name)
        if existing is None:
            raise
        created = existing
    return _with_role_access(ctx, created, defaults_only=defaults_only)


def stdio_user(ctx: AppContext) -> User:
    """Acting user for stdio MCP (ADR 0027 addendum): CTEFORGE_TOKEN when set (that
    token's user and tools), else CTEFORGE_USER with its role's defaults. Never creates
    a user."""
    token = ctx.config.token
    if token is not None:
        return authenticate(ctx, token.get_secret_value())
    return local_user(ctx, create=False, defaults_only=True)


def resolve_user(ctx: AppContext, token: str | None) -> User:
    """Acting user for an HTTP call: the local user when auth is off, else the token's."""
    if not ctx.config.auth_enabled:
        return local_user(ctx)
    return authenticate(ctx, token)


def user_tools(user: User) -> frozenset[Tool]:
    """The acting user's effective tools (what MCP `tools/list` shows; ADR 0027)."""
    return tools_of(user) if user.active else frozenset()


def permitted(user: User, tool: Tool, target: Definition | None = None) -> bool:
    """REVIEW_PROPOSAL / VERIFY consult the target definition (owner, verified state);
    every other tool is membership in the user's effective tools."""
    if tool is Tool.REVIEW_PROPOSAL:
        return can_approve(user, target)
    if tool is Tool.VERIFY:
        return can_verify(user, target)
    return allowed(user, tool)


def require(user: User, tool: Tool, target: Definition | None = None) -> None:
    """Raise PermissionDeniedError unless `user` may use `tool` (on `target`)."""
    if not permitted(user, tool, target):
        what = f" on '{target.id}'" if target is not None else ""
        raise PermissionDeniedError(
            f"User '{user.name}' ({user.kind.value} {user.role}) may not use {tool.value}{what}"
        )


def refuse_tool(ctx: AppContext, user: User, tool_name: str) -> PermissionDeniedError:
    """A call to an MCP tool the caller's token does not show (ADR 0027): record the
    refusal (AuditKind.ADMIN, op=tool_refused, outcome rejected) and return the
    PermissionDeniedError to report. Best effort: a failed audit write is only logged."""
    error = PermissionDeniedError(
        f"Tool '{tool_name}' is not available to this token; ask an admin if you need it"
    )
    _audit.record(
        ctx,
        user,
        AuditKind.ADMIN,
        fail_closed=False,
        error=error,
        outcome=_audit.failure_outcome(error),
        extra={"op": "tool_refused", "tool": tool_name},
    )
    return error
