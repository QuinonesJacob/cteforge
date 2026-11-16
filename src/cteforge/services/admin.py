"""Use-cases: users, roles, tokens and team settings (ADR 0018, DESIGN §8).

Admin only, except that any user may rotate their own token. Tokens are random
(`auth.new_token`), returned ONCE to the caller, and stored only as SHA-256 hashes; they are
never written to the audit log.

Bootstrap: `bootstrap_admin` creates the first admin with no acting user, and only while
the users table is empty (CLI `cteforge user add --bootstrap`). Two concurrent bootstraps
with different names can both succeed; run it once, at setup.

Lock-out guard: the last admin cannot be demoted.

Agents never verify (ADR 0007), so an agent admin may not hand itself a human identity:
it may not create a human user, change a human user's role, or rotate another human
user's token (PermissionDeniedError). It may still manage agent users and settings.
"""

from __future__ import annotations

import re

from cteforge.domain.audit import AuditKind
from cteforge.domain.errors import NotFoundError, PermissionDeniedError, PolicyError
from cteforge.domain.models import TeamMode
from cteforge.domain.users import ActorKind, User, UserRole
from cteforge.services import _audit
from cteforge.services.auth import hash_token, new_token, require
from cteforge.services.cards import TeamSettings
from cteforge.services.context import AppContext, set_team_mode, team_mode
from cteforge.trust.approval import Action

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,63}$")


def _valid_name(name: str) -> str:
    if not _NAME.match(name):
        raise PolicyError(
            "User names are 1-64 characters: letters, digits, '_', '.', '@', '-', "
            "starting with a letter or digit"
        )
    return name


def _add(ctx: AppContext, name: str, role: UserRole, kind: ActorKind) -> tuple[User, str]:
    token = new_token()
    candidate = User(
        name=_valid_name(name), role=UserRole(role), kind=ActorKind(kind), created_at=ctx.now()
    )
    return ctx.store.add_user(candidate, hash_token(token)), token


def _no_agent_over_human(user: User, target_kind: ActorKind, what: str) -> None:
    """ADR 0007: an agent may not mint or take over a human identity (it could then act
    as that human and verify)."""
    if user.kind is ActorKind.AGENT and target_kind is ActorKind.HUMAN:
        raise PermissionDeniedError(
            f"Agent '{user.name}' may not {what} a human user; a person must do it"
        )


def _fields(op: str, **extra: object) -> dict[str, object]:
    return {"extra": {"op": op, **extra}}


def bootstrap_admin(
    ctx: AppContext, name: str, kind: ActorKind = ActorKind.HUMAN
) -> tuple[User, str]:
    """Create the first admin while no users exist. Returns (user, token shown once)."""
    if ctx.store.list_users():
        raise PolicyError("Users already exist; an admin must create new users")
    user, token = _add(ctx, name, UserRole.ADMIN, kind)
    _audit.record(ctx, user, AuditKind.ADMIN, **_fields("bootstrap_admin", name=user.name))
    return user, token


def create_user(
    ctx: AppContext,
    user: User,
    name: str,
    role: UserRole | str,
    kind: ActorKind | str = ActorKind.HUMAN,
) -> tuple[User, str]:
    """Create a user. Returns (user, token): show the token once; it is not stored."""
    fields = _fields("create_user", name=name, role=str(role), kind=str(kind))
    with _audit.audit_failures(ctx, user, AuditKind.ADMIN, **fields):
        require(user, Action.MANAGE_USERS)
        try:
            role, kind = UserRole(role), ActorKind(kind)
        except ValueError:
            raise PolicyError(
                f"role must be one of {[r.value for r in UserRole]}, kind one of "
                f"{[k.value for k in ActorKind]}"
            ) from None
        _no_agent_over_human(user, kind, "create")
        created, token = _add(ctx, name, role, kind)
    _audit.record(ctx, user, AuditKind.ADMIN, **fields)
    return created, token


def list_users(ctx: AppContext, user: User) -> list[User]:
    with _audit.audit_failures(ctx, user, AuditKind.ADMIN, **_fields("list_users")):
        require(user, Action.MANAGE_USERS)
        users = ctx.store.list_users()
    _audit.record(ctx, user, AuditKind.ADMIN, fail_closed=False, **_fields("list_users"))
    return users


def set_role(ctx: AppContext, user: User, name: str, role: UserRole | str) -> User:
    fields = _fields("set_role", name=name, role=str(role))
    with _audit.audit_failures(ctx, user, AuditKind.ADMIN, **fields):
        require(user, Action.MANAGE_USERS)
        try:
            role = UserRole(role)
        except ValueError:
            raise PolicyError(f"role must be one of {[r.value for r in UserRole]}") from None
        target = ctx.store.get_user(name)
        if target is None:
            raise NotFoundError(f"User '{name}' does not exist")
        _no_agent_over_human(user, target.kind, "change the role of")
        if target.role is UserRole.ADMIN and role is not UserRole.ADMIN:
            admins = [u for u in ctx.store.list_users() if u.role is UserRole.ADMIN]
            if len(admins) <= 1:
                raise PolicyError("Cannot demote the last admin; make another admin first")
        updated = ctx.store.set_user_role(name, role)
    _audit.record(ctx, user, AuditKind.ADMIN, **fields)
    return updated


def rotate_token(ctx: AppContext, user: User, name: str) -> str:
    """Issue a new token for `name` (admins, or the user themselves); the old one stops
    working. Returns the new token, shown once."""
    fields = _fields("rotate_token", name=name)
    with _audit.audit_failures(ctx, user, AuditKind.ADMIN, **fields):
        if user.name != name:
            require(user, Action.MANAGE_USERS)
        target = ctx.store.get_user(name)
        if target is None:
            raise NotFoundError(f"User '{name}' does not exist")
        if user.name != name:
            _no_agent_over_human(user, target.kind, "rotate the token of")
        token = new_token()
        ctx.store.set_user_token(name, hash_token(token))
    _audit.record(ctx, user, AuditKind.ADMIN, **fields)
    return token


def get_settings(ctx: AppContext, user: User) -> TeamSettings:
    with _audit.audit_failures(ctx, user, AuditKind.ADMIN, **_fields("get_settings")):
        require(user, Action.MANAGE_SETTINGS)
        settings = TeamSettings(mode=team_mode(ctx))
    _audit.record(ctx, user, AuditKind.ADMIN, fail_closed=False, **_fields("get_settings"))
    return settings


def set_settings(ctx: AppContext, user: User, mode: TeamMode | str) -> TeamSettings:
    fields = _fields("set_settings", mode=str(mode))
    with _audit.audit_failures(ctx, user, AuditKind.ADMIN, **fields):
        require(user, Action.MANAGE_SETTINGS)
        try:
            parsed = TeamMode(mode)
        except ValueError:
            raise PolicyError(f"mode must be one of {[m.value for m in TeamMode]}") from None
        settings = TeamSettings(mode=set_team_mode(ctx, parsed))
    _audit.record(ctx, user, AuditKind.ADMIN, **fields)
    return settings
