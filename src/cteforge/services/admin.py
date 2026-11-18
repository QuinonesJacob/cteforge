"""Use-cases: users, roles, personal access tokens and team settings (ADR 0018, 0027;
DESIGN §8, §9).

Permissions are tools (ADR 0027): list_users (users, roles, token metadata),
manage_user (create users, role, active, other users' token tools/revocation),
manage_role (roles), manage_settings (team mode, default connection). The who-may rules
for actors, admin users, the admin role and tokens are trust/approval.py's
(`can_manage_user`, `can_grant_role`, `can_edit_role`, `can_mint_token`,
`can_revoke_token`, `can_set_token_tools`).

Tokens (`cfg_pat_…`) are generated here, returned ONCE to the caller, and stored only as
SHA-256 hashes; neither the token nor its hash is ever written to the audit log (the token
id and its 12-character display prefix are). Rules:
- Never to an agent-kind caller: an agent can't mint a token on any surface, and
  `create_user` called by an agent returns no token.
- A human mints their own tokens; a human with manage_user mints for anyone.
- No escalation: a token may not mint (or reconfigure) a token of its own user with tools
  the calling token does not hold itself, nor outlive it (a calling token with an expiry
  only mints tokens expiring no later), nor turn an `only` list into `follow_role` (which
  would gain new role defaults later). A token minted by a token for its own user records
  it as `parent_token_id`; revoking a token revokes its descendants. (Tokens minted for
  OTHER users are not linked: that needs manage_user, which is effectively full admin
  power for a human — it can create admins and mint for anyone — by design, ADR 0027.)
- A token's tools are always within its user's role (`Allowed`); asking for more is a
  PolicyError (change the role instead).
- Revoke: one's own; a human admin any; an agent admin only non-admin users' tokens.

Admin agents (agent kind + admin role) may create users (no token returned), change role,
tools and active of non-admin users, revoke non-admin users' tokens, and create/edit roles
other than admin. They may not grant the admin role, touch an admin user, edit the admin
role, or see/mint tokens.

Bootstrap: `bootstrap_admin` creates the first admin with no acting user, and only while
the users table is empty (CLI `cteforge user add --bootstrap`), plus its "default" token.
Lock-out guard: the last active admin cannot be demoted or deactivated.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from collections.abc import Iterable
from typing import Any

from cteforge.domain.access import (
    ADMIN_ONLY_TOOLS,
    ADMIN_ROLE,
    ALL_TOOLS,
    Role,
    TokenInfo,
    Tool,
    ToolMode,
    sorted_tools,
)
from cteforge.domain.audit import AuditKind
from cteforge.domain.errors import NotFoundError, PermissionDeniedError, PolicyError, StoreError
from cteforge.domain.models import TeamMode
from cteforge.domain.users import ActorKind, User, UserRole
from cteforge.services import _audit
from cteforge.services.auth import display_prefix, hash_token, new_token, require
from cteforge.services.cards import (
    MeCard,
    NewToken,
    RoleCard,
    TeamSettings,
    TokenCard,
    UserView,
    role_card,
    token_card,
)
from cteforge.services.context import (
    AppContext,
    default_connection,
    default_connection_issues,
    save_default_connection,
    set_team_mode,
    team_mode,
)
from cteforge.trust.approval import (
    can_edit_role,
    can_grant_role,
    can_manage_user,
    can_mint_token,
    can_revoke_token,
    can_set_token_tools,
    check_role,
    effective_tools,
    is_admin,
    reviews_all,
    role_ceiling,
    role_tools,
    tools_of,
)

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,63}$")
DEFAULT_TOKEN_NAME = "default"
MAX_TOKEN_NAME = 100


def _valid_name(name: str) -> str:
    if not _NAME.match(name):
        raise PolicyError(
            "User names are 1-64 characters: letters, digits, '_', '.', '@', '-', "
            "starting with a letter or digit"
        )
    return name


def _fields(op: str, **extra: object) -> dict[str, object]:
    return {"extra": {"op": op, **extra}}


def _kind(kind: ActorKind | str) -> ActorKind:
    try:
        return ActorKind(kind)
    except ValueError:
        raise PolicyError(f"kind must be one of {[k.value for k in ActorKind]}") from None


def _role(ctx: AppContext, name: str) -> Role:
    role = ctx.store.get_role(str(name))
    if role is None:
        known = [r.name for r in ctx.store.list_roles()]
        raise PolicyError(f"role must be one of {known}")
    return role


def _target(ctx: AppContext, name: str) -> User:
    target = ctx.store.get_user(name)
    if target is None:
        raise NotFoundError(f"User '{name}' does not exist")
    return target


def _denied(actor: User, what: str) -> PermissionDeniedError:
    return PermissionDeniedError(f"User '{actor.name}' ({actor.kind.value}) may not {what}")


def _tools(values: Iterable[Tool | str]) -> frozenset[Tool]:
    try:
        return frozenset(Tool(v) for v in values)
    except ValueError:
        raise PolicyError(f"tools must be among {[t.value for t in Tool]}") from None


# ── Tokens: plumbing ─────────────────────────────────────────────────────────────────


def _token_tools(ctx: AppContext, owner: User, info: TokenInfo) -> frozenset[Tool]:
    role = ctx.store.get_role(owner.role)
    return effective_tools(role, info, owner.kind) if role is not None else frozenset()


def _card(ctx: AppContext, owner: User, info: TokenInfo) -> TokenCard:
    return token_card(info, _token_tools(ctx, owner, info), ctx.now())


def _check_token_spec(
    ctx: AppContext,
    actor: User,
    owner: User,
    mode: ToolMode,
    include: frozenset[Tool],
    exclude: frozenset[Tool],
) -> TokenInfo:
    """Validate a token's tool list for `owner` and return a probe TokenInfo with it.
    Tools outside the owner's role → PolicyError. Self-service may not escalate beyond
    the caller's own current tools → PermissionDeniedError."""
    role = _role(ctx, owner.role)
    ceiling = role_ceiling(role)
    outside = sorted_tools((include | exclude) - ceiling)
    if outside:
        raise PolicyError(
            f"Role '{role.name}' does not allow {[t.value for t in outside]}; a token's "
            "tools stay within its role (change the role to give more)"
        )
    if mode is ToolMode.ONLY and exclude:
        raise PolicyError("'only' tokens list their tools in include; exclude must be empty")
    probe = TokenInfo(
        id="probe",
        user_name=owner.name,
        name="probe",
        prefix="",
        created_at=ctx.now(),
        created_by=actor.name,
        tool_mode=mode,
        include=include,
        exclude=exclude,
    )
    if actor.name == owner.name:
        if actor.token_mode is ToolMode.ONLY and mode is ToolMode.FOLLOW_ROLE:
            raise PolicyError(
                "This token has a fixed tool list ('only these'); it may not give a token "
                "'follow role' tools, which grow with the role. Choose 'only these'."
            )
        wanted = effective_tools(role, probe, owner.kind)
        missing = sorted_tools(wanted - tools_of(actor))
        if missing:
            raise PermissionDeniedError(
                f"This token may not give a token tools it does not hold itself "
                f"({[t.value for t in missing]}); sign in with a token that has them"
            )
    return probe


def _within_caller_expiry(actor: User, expires_at: dt.datetime | None) -> None:
    """A token that expires may only mint tokens (for its own user) that expire no later:
    a short-lived token must not leave a longer-lived one behind."""
    limit = actor.token_expires_at
    if limit is None:
        return
    if expires_at is None or expires_at > limit:
        raise PolicyError(
            f"This token expires at {limit.isoformat(timespec='minutes')}; a token it "
            "generates must expire no later. Give an earlier expiry, or sign in with a "
            "token that does not expire."
        )


def _mint(
    ctx: AppContext,
    actor_name: str,
    owner: User,
    *,
    name: str,
    expires_at: dt.datetime | None = None,
    mode: ToolMode = ToolMode.FOLLOW_ROLE,
    include: frozenset[Tool] = frozenset(),
    exclude: frozenset[Tool] = frozenset(),
    parent_token_id: str | None = None,
) -> tuple[TokenInfo, str]:
    token = new_token()
    info = TokenInfo(
        id=uuid.uuid4().hex,
        user_name=owner.name,
        name=name,
        prefix=display_prefix(token),
        created_at=ctx.now(),
        created_by=actor_name,
        expires_at=expires_at,
        tool_mode=mode,
        include=include,
        exclude=exclude,
        parent_token_id=parent_token_id,
    )
    return ctx.store.add_token(info, hash_token(token)), token


def _token_name(name: str) -> str:
    name = name.strip()
    if not name or len(name) > MAX_TOKEN_NAME:
        raise PolicyError(f"A token needs a name of 1-{MAX_TOKEN_NAME} characters")
    return name


def _owned_token(ctx: AppContext, owner: User, token_id: str) -> TokenInfo:
    info = ctx.store.get_token(token_id)
    if info is None or info.user_name != owner.name:
        raise NotFoundError(f"User '{owner.name}' has no token '{token_id}'")
    return info


# ── Users ────────────────────────────────────────────────────────────────────────────


def bootstrap_admin(
    ctx: AppContext, name: str, kind: ActorKind = ActorKind.HUMAN
) -> tuple[User, str]:
    """Create the first admin while no users exist, with a token named "default".
    Returns (user, token shown once)."""
    if ctx.store.list_users():
        raise PolicyError("Users already exist; an admin must create new users")
    candidate = User(
        name=_valid_name(name), role=UserRole.ADMIN, kind=_kind(kind), created_at=ctx.now()
    )
    user = ctx.store.add_user(candidate)
    info, token = _mint(ctx, user.name, user, name=DEFAULT_TOKEN_NAME)
    _audit.record(
        ctx,
        user,
        AuditKind.ADMIN,
        **_fields("bootstrap_admin", name=user.name, token_id=info.id, prefix=info.prefix),
    )
    return user, token


def create_user(
    ctx: AppContext,
    user: User,
    name: str,
    role: str,
    kind: ActorKind | str = ActorKind.HUMAN,
    *,
    issue_token: bool = True,
) -> tuple[User, str | None]:
    """Create a user (manage_user). With `issue_token` and a HUMAN caller, also a token
    named "default" (follow role), returned once; an agent caller never gets a token
    (ADR 0027: a human admin generates it later). Returns (user, token or None)."""
    extra: dict[str, object] = {"op": "create_user", "name": name, "role": str(role)}
    extra["kind"] = str(kind)
    fields = {"extra": extra}
    with _audit.audit_failures(ctx, user, AuditKind.ADMIN, **fields):
        require(user, Tool.MANAGE_USER)
        parsed_kind = _kind(kind)
        target_role = _role(ctx, str(role))
        if not can_grant_role(user, target_role.name):
            raise _denied(user, f"give anyone the '{target_role.name}' role; a person must")
        candidate = User(
            name=_valid_name(name),
            role=target_role.name,
            kind=parsed_kind,
            created_at=ctx.now(),
        )
        created = ctx.store.add_user(candidate)
        token: str | None = None
        if issue_token and can_mint_token(user, created):
            info, token = _mint(ctx, user.name, created, name=DEFAULT_TOKEN_NAME)
            extra |= {"token_id": info.id, "prefix": info.prefix}
    _audit.record(ctx, user, AuditKind.ADMIN, **fields)
    return created, token


def _view(ctx: AppContext, target: User, tokens: list[TokenInfo] | None = None) -> UserView:
    infos = tokens if tokens is not None else ctx.store.list_tokens(target.name)
    return UserView(
        name=target.name,
        role=target.role,
        kind=target.kind,
        active=target.active,
        created_at=target.created_at,
        tokens=[_card(ctx, target, info) for info in infos if info.user_name == target.name],
    )


def list_users(ctx: AppContext, user: User) -> list[UserView]:
    """Users with their tokens' metadata (names, prefixes, last use; never values)."""
    with _audit.audit_failures(ctx, user, AuditKind.ADMIN, **_fields("list_users")):
        require(user, Tool.LIST_USERS)
        users = ctx.store.list_users()
        tokens = ctx.store.list_tokens()
        views = [_view(ctx, u, [t for t in tokens if t.user_name == u.name]) for u in users]
    _audit.record(ctx, user, AuditKind.ADMIN, fail_closed=False, **_fields("list_users"))
    return views


def _active_admins(ctx: AppContext) -> list[User]:
    return [u for u in ctx.store.list_users() if u.role == ADMIN_ROLE and u.active]


def update_user(
    ctx: AppContext,
    user: User,
    name: str,
    *,
    role: str | None = None,
    active: bool | None = None,
) -> UserView:
    """Change a user's role and/or active flag (manage_user). Agents never touch an
    admin user or grant the admin role; the last active admin cannot be demoted or
    deactivated. Deactivated users cannot authenticate; their tokens are kept."""
    if role is None and active is None:
        raise PolicyError("Give a role and/or active to change")
    changes: dict[str, Any] = {}
    if role is not None:
        changes["role"] = str(role)
    if active is not None:
        changes["active"] = active
    fields = _fields("update_user", name=name, **changes)
    with _audit.audit_failures(ctx, user, AuditKind.ADMIN, **fields):
        require(user, Tool.MANAGE_USER)
        target = _target(ctx, name)
        if not can_manage_user(user, target):
            raise _denied(user, f"change admin user '{name}'; a person must")
        new_role = target.role
        if role is not None:
            new_role = _role(ctx, str(role)).name
            if new_role != target.role and not can_grant_role(user, new_role):
                raise _denied(user, f"give anyone the '{new_role}' role; a person must")
        still_admin = new_role == ADMIN_ROLE and (active if active is not None else target.active)
        if is_admin(target) and target.active and not still_admin:
            if len(_active_admins(ctx)) <= 1:
                raise PolicyError(
                    "Cannot demote or deactivate the last admin; make another admin first"
                )
        updated = target
        if new_role != target.role:
            updated = ctx.store.set_user_role(name, new_role)
        if active is not None and active != target.active:
            updated = ctx.store.set_user_active(name, active)
        view = _view(ctx, updated)
    _audit.record(ctx, user, AuditKind.ADMIN, **fields)
    return view


def set_role(ctx: AppContext, user: User, name: str, role: str) -> UserView:
    return update_user(ctx, user, name, role=role)


def set_active(ctx: AppContext, user: User, name: str, active: bool) -> UserView:
    return update_user(ctx, user, name, active=active)


def me(ctx: AppContext, user: User) -> MeCard:
    """The acting user's own access (any authenticated user; not audited)."""
    role = ctx.store.get_role(user.role)
    allowed = role_tools(role, user.kind) if role is not None else frozenset()
    defaults = role_tools(role, user.kind, defaults_only=True) if role else frozenset()
    return MeCard(
        name=user.name,
        role=user.role,
        kind=user.kind,
        active=user.active,
        created_at=user.created_at,
        tools=sorted_tools(tools_of(user)),
        review_all=reviews_all(user),
        role_allowed=sorted_tools(allowed),
        role_defaults=sorted_tools(defaults),
        token_id=user.token_id,
        token_expires_at=user.token_expires_at,
        token_mode=user.token_mode.value if user.token_mode is not None else None,
    )


# ── Roles ────────────────────────────────────────────────────────────────────────────


def _role_cards(ctx: AppContext, roles: list[Role]) -> list[RoleCard]:
    users = ctx.store.list_users()
    return [role_card(r, sum(1 for u in users if u.role == r.name)) for r in roles]


def list_roles(ctx: AppContext, user: User) -> list[RoleCard]:
    with _audit.audit_failures(ctx, user, AuditKind.ADMIN, **_fields("list_roles")):
        require(user, Tool.LIST_USERS)
        cards = _role_cards(ctx, ctx.store.list_roles())
    _audit.record(ctx, user, AuditKind.ADMIN, fail_closed=False, **_fields("list_roles"))
    return cards


def _checked(role: Role) -> Role:
    problems = check_role(role)
    if problems:
        raise PolicyError("; ".join(problems))
    return role


def _build_role(**values: Any) -> Role:
    try:
        return Role(**values)
    except ValueError as exc:  # pydantic ValidationError (bad name, defaults ⊄ allowed)
        errors = getattr(exc, "errors", None)
        detail = "; ".join(e["msg"] for e in errors()) if callable(errors) else "invalid role"
        raise PolicyError(f"Invalid role: {detail}") from None


def create_role(
    ctx: AppContext,
    user: User,
    name: str,
    *,
    description: str = "",
    allowed: Iterable[Tool | str] = (),
    defaults: Iterable[Tool | str] | None = None,
    review_all: bool = False,
) -> RoleCard:
    """A custom role (manage_role). `defaults` omitted = the same as `allowed`. Admin-only
    tools are refused outside the admin role."""
    allowed_set = _tools(allowed)
    defaults_set = _tools(defaults) if defaults is not None else allowed_set
    fields = _fields(
        "create_role",
        name=name,
        allowed=[t.value for t in sorted_tools(allowed_set)],
        defaults=[t.value for t in sorted_tools(defaults_set)],
        review_all=review_all,
    )
    with _audit.audit_failures(ctx, user, AuditKind.ADMIN, **fields):
        require(user, Tool.MANAGE_ROLE)
        if not can_edit_role(user, name):
            raise _denied(user, f"create or edit the '{name}' role; a person must")
        role = _checked(
            _build_role(
                name=name,
                description=description.strip(),
                allowed=allowed_set,
                defaults=defaults_set,
                review_all=review_all,
                builtin=False,
            )
        )
        if ctx.store.get_role(role.name) is not None:
            raise PolicyError(f"Role '{role.name}' already exists")
        try:
            ctx.store.add_role(role)
        except StoreError:
            raise PolicyError(f"Role '{role.name}' already exists") from None
        card = _role_cards(ctx, [role])[0]
    _audit.record(ctx, user, AuditKind.ADMIN, **fields)
    return card


def update_role(
    ctx: AppContext,
    user: User,
    name: str,
    *,
    description: str | None = None,
    allowed: Iterable[Tool | str] | None = None,
    defaults: Iterable[Tool | str] | None = None,
    review_all: bool | None = None,
) -> RoleCard:
    """Edit a role (manage_role). The admin role's Allowed list and review_all are fixed
    (every tool), and only a human edits it. When only `allowed` shrinks, defaults are
    trimmed to it. Changes apply to every token of the role's users on their next call."""
    allowed_set = _tools(allowed) if allowed is not None else None
    defaults_set = _tools(defaults) if defaults is not None else None
    fields = _fields(
        "update_role",
        name=name,
        allowed=[t.value for t in sorted_tools(allowed_set)] if allowed_set is not None else None,
        defaults=(
            [t.value for t in sorted_tools(defaults_set)] if defaults_set is not None else None
        ),
        review_all=review_all,
    )
    with _audit.audit_failures(ctx, user, AuditKind.ADMIN, **fields):
        require(user, Tool.MANAGE_ROLE)
        if not can_edit_role(user, name):
            raise _denied(user, f"edit the '{name}' role; a person must")
        current = ctx.store.get_role(name)
        if current is None:
            raise NotFoundError(f"Role '{name}' does not exist")
        if current.name == ADMIN_ROLE:
            if allowed_set is not None and allowed_set != ALL_TOOLS:
                raise PolicyError("The admin role always allows every tool")
            if review_all is False:
                raise PolicyError("The admin role always reviews everything")
            if defaults_set is not None and not ADMIN_ONLY_TOOLS <= defaults_set:
                # Follow-role tokens of every admin (the GUI session included) would lose
                # user / role / settings management at once: an admin locking themselves out.
                missing = ", ".join(t.value for t in sorted_tools(ADMIN_ONLY_TOOLS - defaults_set))
                raise PolicyError(
                    f"The admin role's On-by-default tools must keep the admin tools ({missing}); "
                    "narrow a single token instead"
                )
        new_allowed = allowed_set if allowed_set is not None else current.allowed
        if defaults_set is not None:
            new_defaults = defaults_set
        else:
            new_defaults = current.defaults & new_allowed
        role = _checked(
            _build_role(
                name=current.name,
                description=(
                    description.strip() if description is not None else current.description
                ),
                allowed=new_allowed,
                defaults=new_defaults,
                review_all=review_all if review_all is not None else current.review_all,
                builtin=current.builtin,
            )
        )
        updated = ctx.store.update_role(role)
        card = _role_cards(ctx, [updated])[0]
    _audit.record(ctx, user, AuditKind.ADMIN, **fields)
    return card


def delete_role(ctx: AppContext, user: User, name: str) -> None:
    """Delete a custom role nobody holds (manage_role). Built-in roles stay."""
    fields = _fields("delete_role", name=name)
    with _audit.audit_failures(ctx, user, AuditKind.ADMIN, **fields):
        require(user, Tool.MANAGE_ROLE)
        if not can_edit_role(user, name):
            raise _denied(user, f"delete the '{name}' role; a person must")
        current = ctx.store.get_role(name)
        if current is None:
            raise NotFoundError(f"Role '{name}' does not exist")
        if current.builtin:
            raise PolicyError(f"'{name}' is a built-in role; it can be edited, not deleted")
        if any(u.role == name for u in ctx.store.list_users()):
            raise PolicyError(f"Role '{name}' is held by users; give them another role first")
        try:
            ctx.store.delete_role(name)
        except StoreError as exc:  # Taken by a user meanwhile.
            raise PolicyError(str(exc)) from None
    _audit.record(ctx, user, AuditKind.ADMIN, **fields)


# ── Tokens ───────────────────────────────────────────────────────────────────────────


def list_tokens(ctx: AppContext, user: User, name: str | None = None) -> list[TokenCard]:
    """Token metadata (never values) of `name` (default: the caller). Own tokens need no
    tool; another user's need list_users."""
    owner_name = name or user.name
    fields = _fields("list_tokens", name=owner_name)
    with _audit.audit_failures(ctx, user, AuditKind.ADMIN, **fields):
        if owner_name != user.name:
            require(user, Tool.LIST_USERS)
        owner = _target(ctx, owner_name)
        cards = [_card(ctx, owner, info) for info in ctx.store.list_tokens(owner.name)]
    _audit.record(ctx, user, AuditKind.ADMIN, fail_closed=False, **fields)
    return cards


def create_token(
    ctx: AppContext,
    user: User,
    owner_name: str | None = None,
    *,
    name: str,
    expires_at: dt.datetime | None = None,
    tool_mode: ToolMode | str = ToolMode.FOLLOW_ROLE,
    include: Iterable[Tool | str] = (),
    exclude: Iterable[Tool | str] = (),
) -> NewToken:
    """Generate a token for `owner_name` (default: the caller). Returns it ONCE.
    Never for an agent-kind caller. A human for themselves (no escalation beyond the
    calling token's own tools), a human with manage_user for anyone."""
    owner_name = owner_name or user.name
    extra: dict[str, object] = {"op": "create_token", "name": owner_name, "token_name": name}
    fields = {"extra": extra}
    with _audit.audit_failures(ctx, user, AuditKind.ADMIN, **fields):
        if user.kind is ActorKind.AGENT:
            raise _denied(user, "generate tokens; agents never see tokens (ADR 0027)")
        if owner_name != user.name:
            require(user, Tool.MANAGE_USER)  # before revealing whether the user exists
        owner = _target(ctx, owner_name)
        if not can_mint_token(user, owner):
            raise _denied(user, f"generate tokens for '{owner_name}'")
        if not owner.active:
            raise PolicyError(f"User '{owner_name}' is deactivated; activate them first")
        try:
            mode = ToolMode(tool_mode)
        except ValueError:
            raise PolicyError(f"tool_mode must be one of {[m.value for m in ToolMode]}") from None
        if expires_at is not None:
            if expires_at.utcoffset() is None:
                raise PolicyError("expires_at must be timezone-aware")
            if expires_at <= ctx.now():
                raise PolicyError("expires_at must be in the future")
        include_set, exclude_set = _tools(include), _tools(exclude)
        _check_token_spec(ctx, user, owner, mode, include_set, exclude_set)
        parent = None
        if owner.name == user.name and user.token_id is not None:
            _within_caller_expiry(user, expires_at)
            parent = user.token_id
        info, token = _mint(
            ctx,
            user.name,
            owner,
            name=_token_name(name),
            expires_at=expires_at,
            mode=mode,
            include=include_set,
            exclude=exclude_set,
            parent_token_id=parent,
        )
        extra |= {"token_id": info.id, "prefix": info.prefix}
        card = _card(ctx, owner, info)
    _audit.record(ctx, user, AuditKind.ADMIN, **fields)
    return NewToken(token=token, info=card)


def revoke_token(
    ctx: AppContext, user: User, token_id: str, owner_name: str | None = None
) -> TokenCard:
    """Revoke a token (effective on its next call) and every token minted from it. Own
    tokens; a human admin any; an agent admin only non-admin users'."""
    owner_name = owner_name or user.name
    fields = _fields("revoke_token", name=owner_name, token_id=token_id)
    with _audit.audit_failures(ctx, user, AuditKind.ADMIN, **fields):
        if owner_name != user.name:
            require(user, Tool.MANAGE_USER)  # before revealing whether the user exists
        owner = _target(ctx, owner_name)
        if not can_revoke_token(user, owner):
            raise _denied(user, f"revoke tokens of '{owner_name}'")
        _owned_token(ctx, owner, token_id)
        info = ctx.store.revoke_token(token_id, ctx.now())
        card = _card(ctx, owner, info)
    _audit.record(ctx, user, AuditKind.ADMIN, **fields)
    return card


def set_token_tools(
    ctx: AppContext,
    user: User,
    token_id: str,
    owner_name: str | None = None,
    *,
    tool_mode: ToolMode | str,
    include: Iterable[Tool | str] = (),
    exclude: Iterable[Tool | str] = (),
) -> TokenCard:
    """Change a token's tool list (within its user's role). Own tokens (no escalation
    beyond the calling token's tools), or whoever may manage the owner (agents never an
    admin user's)."""
    owner_name = owner_name or user.name
    include_set, exclude_set = _tools(include), _tools(exclude)
    fields = _fields(
        "set_token_tools",
        name=owner_name,
        token_id=token_id,
        tool_mode=str(tool_mode),
        include=[t.value for t in sorted_tools(include_set)],
        exclude=[t.value for t in sorted_tools(exclude_set)],
    )
    with _audit.audit_failures(ctx, user, AuditKind.ADMIN, **fields):
        if owner_name != user.name:
            require(user, Tool.MANAGE_USER)  # before revealing whether the user exists
        owner = _target(ctx, owner_name)
        if not can_set_token_tools(user, owner):
            raise _denied(user, f"change the tools of '{owner_name}''s tokens")
        try:
            mode = ToolMode(tool_mode)
        except ValueError:
            raise PolicyError(f"tool_mode must be one of {[m.value for m in ToolMode]}") from None
        current = _owned_token(ctx, owner, token_id)
        if current.revoked_at is not None:
            raise PolicyError("This token is revoked")
        _check_token_spec(ctx, user, owner, mode, include_set, exclude_set)
        info = ctx.store.set_token_tools(token_id, mode, include_set, exclude_set)
        card = _card(ctx, owner, info)
    _audit.record(ctx, user, AuditKind.ADMIN, **fields)
    return card


# ── Settings ─────────────────────────────────────────────────────────────────────────


def _settings(ctx: AppContext) -> TeamSettings:
    return TeamSettings(
        mode=team_mode(ctx),
        default_connection=default_connection(ctx),
        effective_default_connection=ctx.warehouses.default_name,
        issues=default_connection_issues(ctx),
    )


def get_settings(ctx: AppContext, user: User) -> TeamSettings:
    with _audit.audit_failures(ctx, user, AuditKind.ADMIN, **_fields("get_settings")):
        require(user, Tool.MANAGE_SETTINGS)
        settings = _settings(ctx)
    _audit.record(ctx, user, AuditKind.ADMIN, fail_closed=False, **_fields("get_settings"))
    return settings


def set_settings(ctx: AppContext, user: User, mode: TeamMode | str) -> TeamSettings:
    fields = _fields("set_settings", mode=str(mode))
    with _audit.audit_failures(ctx, user, AuditKind.ADMIN, **fields):
        require(user, Tool.MANAGE_SETTINGS)
        try:
            parsed = TeamMode(mode)
        except ValueError:
            raise PolicyError(f"mode must be one of {[m.value for m in TeamMode]}") from None
        set_team_mode(ctx, parsed)
        settings = _settings(ctx)
    _audit.record(ctx, user, AuditKind.ADMIN, **fields)
    return settings


def set_default_connection(ctx: AppContext, user: User, name: str | None) -> TeamSettings:
    """Set (or, with None, clear) the team's default connection (ADR 0021): the warehouse
    profile used when a caller names none, whose dialect is the default dialect.
    manage_settings. A name that is not a configured profile → NotFoundError. Existing
    definitions keep their own profile. A blank name clears it."""
    name = (name or "").strip() or None
    fields = _fields("set_default_connection", default_connection=name)
    with _audit.audit_failures(ctx, user, AuditKind.ADMIN, **fields):
        require(user, Tool.MANAGE_SETTINGS)
        if name is not None and not ctx.warehouses.has(name):
            raise NotFoundError(
                f"Warehouse profile '{name}' is not configured "
                f"(configured: {', '.join(ctx.warehouses.names()) or 'none'})"
            )
        save_default_connection(ctx, name)
        settings = _settings(ctx)
    _audit.record(ctx, user, AuditKind.ADMIN, **fields)
    return settings
