"""Permission and approval policy (DESIGN §18, ADR 0007, ADR 0018). Pure: no I/O."""

from __future__ import annotations

from enum import StrEnum

from cteforge.domain.issues import Issue, IssueCode, has_errors
from cteforge.domain.models import Definition, DefinitionState, TeamMode
from cteforge.domain.users import ActorKind, User, UserRole, role_at_least


class Action(StrEnum):
    SEARCH = "search"
    DESCRIBE = "describe"
    RUN = "run"
    PROPOSE = "propose"
    APPROVE = "approve"
    VERIFY = "verify"
    MANAGE_USERS = "manage_users"
    MANAGE_SETTINGS = "manage_settings"


# Minimum role per action (ADR 0018).
ROLE_MINIMUM: dict[Action, UserRole] = {
    Action.SEARCH: UserRole.VIEWER,
    Action.DESCRIBE: UserRole.VIEWER,
    Action.RUN: UserRole.VIEWER,
    Action.PROPOSE: UserRole.CONTRIBUTOR,
    Action.APPROVE: UserRole.APPROVER,
    Action.VERIFY: UserRole.APPROVER,
    Action.MANAGE_USERS: UserRole.ADMIN,
    Action.MANAGE_SETTINGS: UserRole.ADMIN,
}


def _is_agent(user: User) -> bool:
    return user.kind is ActorKind.AGENT


def _is_owner(user: User, target: Definition | None) -> bool:
    """Owner of the target, and at least a contributor (a viewer owner can't act)."""
    return (
        target is not None
        and target.owner is not None
        and target.owner == user.name
        and role_at_least(user.role, UserRole.CONTRIBUTOR)
    )


def allowed(user: User, action: Action) -> bool:
    """Role minimums per ADR 0018. Agents can never verify (ADR 0007)."""
    if action is Action.VERIFY and _is_agent(user):
        return False
    return role_at_least(user.role, ROLE_MINIMUM[action])


def _own_work(user: User, author: str | None) -> bool:
    """Four-eyes rule (ADR 0018 addendum): `user` authored the work and is not a human
    admin. Only human admins are exempt (so single-user local mode stays workable); an
    agent admin never approves or verifies its own work."""
    if author is None or author != user.name:
        return False
    return not (user.role is UserRole.ADMIN and not _is_agent(user))


def can_approve(user: User, target: Definition | None, *, author: str | None = None) -> bool:
    """Approver/admin, or the target definition's owner. Changes to a verified definition
    always need a human (ADR 0007). `target=None` means a new definition. `author` is the
    proposal's author: nobody but a human admin may approve their own proposal (four-eyes rule,
    ADR 0018 addendum); None skips that check."""
    if target is not None and target.state is DefinitionState.VERIFIED and _is_agent(user):
        return False
    if _own_work(user, author):
        return False
    return allowed(user, Action.APPROVE) or _is_owner(user, target)


def can_verify(user: User, target: Definition | None, *, author: str | None = None) -> bool:
    """Humans only (ADR 0007), and only approver/admin (ADR 0018). Owning a definition does
    not grant verify: `owner` comes from the proposed YAML, so a proposer could otherwise
    name themselves owner and verify their own work. `author` is the author of the version
    being verified: nobody but a human admin may verify their own version (four-eyes rule,
    ADR 0018 addendum); None skips that check. `target` does not widen the check."""
    del target
    return allowed(user, Action.VERIFY) and not _own_work(user, author)


def can_auto_apply(mode: TeamMode, target: Definition | None, checks: list[Issue]) -> bool:
    """DESIGN §18: open mode, no error issues, checks complete, target new or not verified."""
    if mode is not TeamMode.OPEN:
        return False
    if has_errors(checks):
        return False
    if any(i.code is IssueCode.CHECKS_INCOMPLETE for i in checks):
        return False
    return target is None or target.state is not DefinitionState.VERIFIED
