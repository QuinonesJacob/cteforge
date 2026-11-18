"""Permission and approval policy (DESIGN §18; ADR 0007, 0018, 0027). Pure: no I/O.

The permission unit is the tool (domain/access.py). A user's effective tools are resolved
at authentication (`effective_tools`: the role's ceiling ∩ the token's list) and carried
on `User.tools`; a User without them falls back to its built-in role (`BUILTIN_ROLES`,
an unknown role name → no tools). Everything here checks membership and the few rules
that depend on the actor (kind, role name) or the target (owner, state, author).

Agents: verify only with the admin role, and never on their own work (four-eyes with no
exemption for agents). Admin agents may not touch admin users, grant or edit the admin
role, or see or mint tokens (ADR 0027).
"""

from __future__ import annotations

from collections.abc import Iterable

from cteforge.domain.access import (
    ADMIN_ONLY_TOOLS,
    ADMIN_ROLE,
    ALL_TOOLS,
    BUILTIN_ROLES,
    Role,
    TokenInfo,
    Tool,
    ToolMode,
)
from cteforge.domain.issues import Issue, IssueCode, has_errors
from cteforge.domain.models import Definition, DefinitionState, TeamMode
from cteforge.domain.users import ActorKind, User

# ── Effective tools ──────────────────────────────────────────────────────────────────


def role_ceiling(role: Role) -> frozenset[Tool]:
    """What a role may ever allow: every tool for admin (its list cannot be edited);
    never an admin-only tool for any other role, whatever is stored."""
    if role.name == ADMIN_ROLE:
        return ALL_TOOLS
    return frozenset(role.allowed) - ADMIN_ONLY_TOOLS


def _for_kind(tools: Iterable[Tool], role: Role, kind: ActorKind) -> frozenset[Tool]:
    """An agent verifies only with the admin role (ADR 0027): other roles' verify is
    ignored for agent-kind users."""
    out = frozenset(tools)
    if kind is ActorKind.AGENT and role.name != ADMIN_ROLE:
        out -= {Tool.VERIFY}
    return out


def role_tools(role: Role, kind: ActorKind, *, defaults_only: bool = False) -> frozenset[Tool]:
    """A user's tools with no token involved: the role's allowed list (CLI, auth-off
    HTTP: a person at the machine), or its defaults (stdio MCP by CTEFORGE_USER)."""
    ceiling = role_ceiling(role)
    chosen = (frozenset(role.defaults) & ceiling) if defaults_only else ceiling
    return _for_kind(chosen, role, kind)


def effective_tools(role: Role, token: TokenInfo, kind: ActorKind) -> frozenset[Tool]:
    """ADR 0027 addendum: role.allowed ∩ (follow_role: (role.defaults ∪ include) −
    exclude; only: include), minus verify for an agent whose role is not admin."""
    if token.tool_mode is ToolMode.ONLY:
        wanted = frozenset(token.include)
    else:
        wanted = (frozenset(role.defaults) | token.include) - token.exclude
    return _for_kind(wanted & role_ceiling(role), role, kind)


def check_role(role: Role) -> list[str]:
    """Problems with a role definition (empty = fine): admin-only tools outside the admin
    role, the admin role not allowing every tool, defaults not within allowed."""
    problems = []
    if role.name == ADMIN_ROLE:
        if frozenset(role.allowed) != ALL_TOOLS:
            problems.append("the admin role always allows every tool")
        if not role.review_all:
            problems.append("the admin role always reviews everything")
    else:
        bad = sorted(t.value for t in frozenset(role.allowed) & ADMIN_ONLY_TOOLS)
        if bad:
            problems.append(f"only the admin role may allow {bad}")
    if not frozenset(role.defaults) <= frozenset(role.allowed):
        problems.append("on-by-default tools must be allowed")
    return problems


# ── The acting user ──────────────────────────────────────────────────────────────────


def _builtin(user: User) -> Role | None:
    return BUILTIN_ROLES.get(user.role)


def tools_of(user: User) -> frozenset[Tool]:
    """The acting user's effective tools: resolved ones, else its built-in role's
    defaults (for the user's kind), else none."""
    if user.tools is not None:
        return user.tools
    role = _builtin(user)
    return role_tools(role, user.kind, defaults_only=True) if role is not None else frozenset()


def reviews_all(user: User) -> bool:
    if user.review_all is not None:
        return user.review_all
    role = _builtin(user)
    return role.review_all if role is not None else False


def is_admin(user: User) -> bool:
    return user.role == ADMIN_ROLE


def is_agent(user: User) -> bool:
    return user.kind is ActorKind.AGENT


def allowed(user: User, tool: Tool) -> bool:
    """`user` holds `tool` (deactivated users hold nothing)."""
    if not user.active:
        return False
    if tool is Tool.VERIFY and is_agent(user) and not is_admin(user):
        return False
    return tool in tools_of(user)


# ── Review ───────────────────────────────────────────────────────────────────────────


def _own_work(user: User, author: str | None) -> bool:
    """Four-eyes rule (ADR 0018 addendum, ADR 0027): `user` authored the work and is not
    a human admin. Only human admins are exempt (so single-user local mode stays
    workable); an agent admin never approves or verifies its own work."""
    if author is None or author != user.name:
        return False
    return not (is_admin(user) and not is_agent(user))


def _is_owner(user: User, target: Definition | None) -> bool:
    return target is not None and target.owner is not None and target.owner == user.name


def may_change_verified(user: User) -> bool:
    """Changes to verified work need a person, or an agent with the admin role
    (ADR 0007 as amended by ADR 0027)."""
    return not is_agent(user) or is_admin(user)


def can_approve(user: User, target: Definition | None, *, author: str | None = None) -> bool:
    """Approve/reject a change to `target` (None = a new definition): needs the
    review_proposal tool, and either review_all or ownership of the target. A VERIFIED
    target needs a human or an admin agent. `author` is the proposal's author: nobody but
    a human admin may approve their own proposal (four-eyes); None skips that check."""
    if not allowed(user, Tool.REVIEW_PROPOSAL):
        return False
    if target is not None and target.state is DefinitionState.VERIFIED:
        if not may_change_verified(user):
            return False
    if _own_work(user, author):
        return False
    return reviews_all(user) or _is_owner(user, target)


def can_verify(user: User, target: Definition | None, *, author: str | None = None) -> bool:
    """The verify tool; an agent only with the admin role. Owning a definition does not
    grant verify (`owner` comes from the proposed YAML). `author` is the author of the
    version being verified: nobody but a human admin may verify their own version
    (four-eyes, no exemption for agents); None skips that check. `target` does not widen
    the check."""
    del target
    return allowed(user, Tool.VERIFY) and not _own_work(user, author)


def can_auto_apply(mode: TeamMode, target: Definition | None, checks: list[Issue]) -> bool:
    """DESIGN §18: open mode, no error issues, checks complete, target new or not verified."""
    if mode is not TeamMode.OPEN:
        return False
    if has_errors(checks):
        return False
    if any(i.code is IssueCode.CHECKS_INCOMPLETE for i in checks):
        return False
    return target is None or target.state is not DefinitionState.VERIFIED


def join_may_auto_apply(action: str, author_kind: ActorKind) -> bool:
    """ADR 0026: an edge an AGENT adds (one no definition contains) never auto-applies,
    in any mode; it waits for a person, so an agent cannot raise trust labels by declaring
    joins. Annotations and people's additions follow `can_auto_apply`."""
    return not (action == "add" and author_kind is ActorKind.AGENT)


def confirms_join(decider_kind: ActorKind, *, auto: bool) -> bool:
    """Whether a decision makes a join edge confirmed (and so known): a PERSON approving
    it. Auto-apply is not an approval, and an admin agent's approval does not confirm
    (ADR 0026: joins become known only through definitions or people)."""
    return decider_kind is ActorKind.HUMAN and not auto


# ── Users, roles and tokens (ADR 0027: admin agents) ─────────────────────────────────


def can_manage_user(actor: User, target: User) -> bool:
    """Change `target`'s role, tools or active flag, or revoke its tokens: manage_user,
    and an agent never touches an admin user (itself included)."""
    if not allowed(actor, Tool.MANAGE_USER):
        return False
    return not (is_agent(actor) and is_admin(target))


def can_grant_role(actor: User, role_name: str) -> bool:
    """Give a user the role `role_name` (also when creating one): manage_user; only a
    human may grant the admin role."""
    if not allowed(actor, Tool.MANAGE_USER):
        return False
    return not (is_agent(actor) and role_name == ADMIN_ROLE)


def can_edit_role(actor: User, role_name: str) -> bool:
    """Create, edit or delete the role `role_name`: manage_role; only a human edits the
    admin role (and then only its description and defaults)."""
    if not allowed(actor, Tool.MANAGE_ROLE):
        return False
    return not (is_agent(actor) and role_name == ADMIN_ROLE)


def can_mint_token(actor: User, owner: User) -> bool:
    """Generate a token for `owner`: never an agent caller (agents never see tokens); a
    human for themselves, or a human with manage_user for anyone."""
    if is_agent(actor) or not actor.active:
        return False
    return actor.name == owner.name or allowed(actor, Tool.MANAGE_USER)


def can_revoke_token(actor: User, owner: User) -> bool:
    """Revoke a token of `owner`: one's own; a human with manage_user any; an agent with
    manage_user only a non-admin user's."""
    if not actor.active:
        return False
    if actor.name == owner.name:
        return True
    return can_manage_user(actor, owner)


def can_set_token_tools(actor: User, owner: User) -> bool:
    """Change a token's tool list: the owner (within the no-escalation rule services
    apply), or whoever may manage the owner."""
    if not actor.active:
        return False
    if actor.name == owner.name:
        return True
    return can_manage_user(actor, owner)
