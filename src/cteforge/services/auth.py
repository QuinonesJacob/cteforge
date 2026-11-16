"""Authentication and permission checks (ADR 0018).

Tokens are random (`new_token`), shown once, and stored only as SHA-256 hashes. A failed
lookup raises one generic PermissionDeniedError whatever the reason, so callers cannot
probe which tokens or users exist. Permission policy itself lives in trust/approval.py.
"""

from __future__ import annotations

import hashlib
import secrets

from cteforge.domain.errors import PermissionDeniedError, StoreError
from cteforge.domain.models import Definition
from cteforge.domain.users import ActorKind, User, UserRole
from cteforge.services.context import AppContext
from cteforge.trust.approval import Action, allowed, can_approve, can_verify

_INVALID_TOKEN = "Invalid or missing token"
_MAX_TOKEN_LENGTH = 512  # Far above token_urlsafe(32); refuses absurd inputs cheaply.


def hash_token(token: str) -> str:
    """SHA-256 hex digest: the only form in which a token is stored."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> str:
    """A fresh random bearer token (43 URL-safe chars). Show it once; store its hash."""
    return secrets.token_urlsafe(32)


def authenticate(ctx: AppContext, token: str | None) -> User:
    """Bearer token → User. Any failure (missing, malformed, unknown) raises the same
    PermissionDeniedError with a generic message."""
    if not token or len(token) > _MAX_TOKEN_LENGTH:
        raise PermissionDeniedError(_INVALID_TOKEN)
    user = ctx.store.user_by_token(hash_token(token))
    if user is None:
        raise PermissionDeniedError(_INVALID_TOKEN)
    return user


def local_user(ctx: AppContext) -> User:
    """The local user named by CTEFORGE_USER.

    Only for stdio MCP, the CLI, or HTTP with CTEFORGE_AUTH=off (bound to localhost; the
    API enforces that). An existing user is used as stored (role and kind). A missing user
    is created as a human admin ONLY while the store has no users at all (first local use,
    like `admin.bootstrap_admin`); in a store that already has users, an unknown name raises
    PermissionDeniedError, so a typo or an agent's name never becomes a human admin. Its
    stored token hash is of a random token nobody ever sees, so the local user cannot be
    used over authenticated HTTP until an admin issues a token.
    """
    name = ctx.config.user
    user = ctx.store.get_user(name)
    if user is not None:
        return user
    if ctx.store.list_users():
        raise PermissionDeniedError(
            f"Local user '{name}' does not exist; ask an admin to create it "
            "(CTEFORGE_USER must name an existing user once any user exists)"
        )
    candidate = User(name=name, role=UserRole.ADMIN, kind=ActorKind.HUMAN, created_at=ctx.now())
    try:
        return ctx.store.add_user(candidate, hash_token(new_token()))
    except StoreError:
        # Created concurrently by another call: use that one.
        existing = ctx.store.get_user(name)
        if existing is None:
            raise
        return existing


def resolve_user(ctx: AppContext, token: str | None) -> User:
    """Acting user for an HTTP call: the local user when auth is off, else the token's."""
    if not ctx.config.auth_enabled:
        return local_user(ctx)
    return authenticate(ctx, token)


def permitted(user: User, action: Action, target: Definition | None = None) -> bool:
    """APPROVE/VERIFY consult the target definition (owner, verified state); others are
    role minimums (ADR 0018). Agents never verify (ADR 0007)."""
    if action is Action.APPROVE:
        return can_approve(user, target)
    if action is Action.VERIFY:
        return can_verify(user, target)
    return allowed(user, action)


def require(user: User, action: Action, target: Definition | None = None) -> None:
    """Raise PermissionDeniedError unless `user` may perform `action` (on `target`)."""
    if not permitted(user, action, target):
        what = f" on '{target.id}'" if target is not None else ""
        raise PermissionDeniedError(
            f"User '{user.name}' ({user.kind.value} {user.role.value}) may not {action.value}{what}"
        )
