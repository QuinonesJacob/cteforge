"""Users, roles, tokens and team settings (ADR 0018, DESIGN §8)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

import pytest
from sqlalchemy import select

from cteforge.domain.access import Tool
from cteforge.domain.audit import AuditKind, AuditOutcome
from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import (
    NotFoundError,
    PermissionDeniedError,
    PolicyError,
    StoreError,
)
from cteforge.domain.issues import IssueCode
from cteforge.domain.models import TeamMode
from cteforge.domain.users import ActorKind, User, UserRole
from cteforge.services import admin
from cteforge.services.auth import authenticate, hash_token
from cteforge.services.context import (
    DEFAULT_CONNECTION_SETTING,
    AppConfig,
    AppContext,
    open_context,
    team_mode,
)
from cteforge.store import tables

FIXED = dt.datetime(2026, 11, 16, 9, 30, tzinfo=dt.UTC)


@pytest.fixture
def ctx() -> Iterator[AppContext]:
    with open_context(AppConfig(db_url="sqlite://"), clock=lambda: FIXED) as c:
        yield c


@pytest.fixture
def root(ctx: AppContext) -> User:
    user, _ = admin.bootstrap_admin(ctx, "root")
    return user


def _stored_token_hashes(ctx: AppContext) -> list[str]:
    with ctx.store._engine.connect() as conn:  # test-only peek at the raw columns
        return list(conn.execute(select(tables.tokens.c.token_sha256)).scalars()) + list(
            conn.execute(select(tables.users.c.token_sha256)).scalars()
        )


def _no_token_in_audit(ctx: AppContext, *tokens: str) -> None:
    for event in ctx.audit.list_events(limit=1000):
        dumped = event.model_dump_json()
        for token in tokens:
            assert token not in dumped and hash_token(token) not in dumped


def test_bootstrap_only_while_no_users(ctx: AppContext) -> None:
    user, token = admin.bootstrap_admin(ctx, "root")
    assert user.role == UserRole.ADMIN and user.kind is ActorKind.HUMAN
    assert token.startswith("cfg_pat_")
    assert authenticate(ctx, token).name == "root"
    [info] = ctx.store.list_tokens("root")
    assert info.name == "default" and info.prefix == token[:12]
    with pytest.raises(PolicyError):
        admin.bootstrap_admin(ctx, "second")
    event = ctx.audit.list_events(kind=AuditKind.ADMIN)[0]
    assert event.actor == "root" and event.extra["op"] == "bootstrap_admin"
    assert event.extra["prefix"] == token[:12]
    _no_token_in_audit(ctx, token)


def test_create_user_token_lifecycle(ctx: AppContext, root: User) -> None:
    bot, token = admin.create_user(ctx, root, "claude-agent", "contributor", "agent")
    assert bot.role == UserRole.CONTRIBUTOR and bot.kind is ActorKind.AGENT
    assert bot.created_at == FIXED and token is not None
    acting = authenticate(ctx, token)
    assert acting.name == "claude-agent" and Tool.PROPOSE_DEFINITION in acting.tools
    hashes = _stored_token_hashes(ctx)
    assert token not in hashes and hash_token(token) in hashes  # never stored raw
    second = admin.create_token(ctx, root, "claude-agent", name="CI")
    assert second.token != token and authenticate(ctx, second.token).name == "claude-agent"
    admin.revoke_token(ctx, root, second.info.id, "claude-agent")
    with pytest.raises(PermissionDeniedError):
        authenticate(ctx, second.token)  # revoked: stops on the next call
    assert authenticate(ctx, token).name == "claude-agent"  # the other one still works
    _no_token_in_audit(ctx, token, second.token)


def test_humans_mint_and_revoke_their_own_tokens(ctx: AppContext, root: User) -> None:
    alice, _ = admin.create_user(ctx, root, "alice", UserRole.CONTRIBUTOR)
    admin.create_user(ctx, root, "bob", UserRole.CONTRIBUTOR)
    mine = admin.create_token(ctx, alice, name="Claude Code")
    assert mine.info.user_name == "alice" and mine.info.created_by == "alice"
    assert authenticate(ctx, mine.token).name == "alice"
    assert {t.name for t in admin.list_tokens(ctx, alice)} == {"default", "Claude Code"}
    with pytest.raises(PermissionDeniedError):
        admin.create_token(ctx, alice, "bob", name="x")
    with pytest.raises(PermissionDeniedError):
        admin.list_tokens(ctx, alice, "bob")
    bobs = admin.list_tokens(ctx, root, "bob")
    with pytest.raises(PermissionDeniedError):
        admin.revoke_token(ctx, alice, bobs[0].id, "bob")
    with pytest.raises(NotFoundError):
        admin.revoke_token(ctx, alice, bobs[0].id)  # not one of alice's
    revoked = admin.revoke_token(ctx, alice, mine.info.id)
    assert revoked.state == "revoked" and revoked.tools == []
    with pytest.raises(NotFoundError):
        admin.create_token(ctx, root, "nobody", name="x")


def test_token_cards_never_carry_values(ctx: AppContext, root: User) -> None:
    made = admin.create_token(ctx, root, name="x")
    dumped = [
        *(c.model_dump_json() for c in admin.list_tokens(ctx, root)),
        *(v.model_dump_json() for v in admin.list_users(ctx, root)),
        made.info.model_dump_json(),
    ]
    for text in dumped:
        assert made.token not in text and hash_token(made.token) not in text


def test_token_tools_stay_within_the_role(ctx: AppContext, root: User) -> None:
    admin.create_user(ctx, root, "vic", UserRole.VIEWER)
    with pytest.raises(PolicyError, match="does not allow"):
        admin.create_token(ctx, root, "vic", name="x", include=[Tool.PROPOSE_DEFINITION])
    with pytest.raises(PolicyError, match="does not allow"):
        admin.create_token(ctx, root, "vic", name="x", tool_mode="only", include=["verify"])
    with pytest.raises(PolicyError, match="exclude"):
        admin.create_token(ctx, root, "vic", name="x", tool_mode="only", exclude=["search"])
    with pytest.raises(PolicyError):
        admin.create_token(ctx, root, "vic", name="x", include=["teleport"])
    narrow = admin.create_token(ctx, root, "vic", name="bot", tool_mode="only", include=["search"])
    assert narrow.info.tools == [Tool.SEARCH]
    assert authenticate(ctx, narrow.token).tools == {Tool.SEARCH}
    changed = admin.set_token_tools(
        ctx, root, narrow.info.id, "vic", tool_mode="follow_role", exclude=["check_and_run"]
    )
    assert Tool.CHECK_AND_RUN not in changed.tools and Tool.SEARCH in changed.tools
    assert Tool.CHECK_AND_RUN not in authenticate(ctx, narrow.token).tools


def test_token_names_and_expiry_are_validated(ctx: AppContext, root: User) -> None:
    with pytest.raises(PolicyError, match="name"):
        admin.create_token(ctx, root, name="   ")
    with pytest.raises(PolicyError, match="future"):
        admin.create_token(ctx, root, name="x", expires_at=FIXED)
    with pytest.raises(PolicyError, match="timezone"):
        admin.create_token(ctx, root, name="x", expires_at=dt.datetime(2030, 1, 1))
    later = admin.create_token(ctx, root, name="x", expires_at=FIXED + dt.timedelta(days=1))
    assert later.info.expires_at == FIXED + dt.timedelta(days=1)


def test_no_escalation_through_self_service(ctx: AppContext, root: User) -> None:
    """A narrow token may not mint (or widen) a token of its own user beyond itself."""
    narrow = admin.create_token(ctx, root, name="narrow", tool_mode="only", include=["search"])
    as_narrow = authenticate(ctx, narrow.token)
    with pytest.raises(PermissionDeniedError, match="does not hold"):
        admin.create_token(ctx, as_narrow, name="wide")
    with pytest.raises(PermissionDeniedError):
        admin.create_token(ctx, as_narrow, "someone", name="x")  # no manage_user either
    small = admin.create_token(ctx, as_narrow, name="same", tool_mode="only", include=["search"])
    assert small.info.tools == [Tool.SEARCH]
    with pytest.raises(PermissionDeniedError, match="does not hold"):
        admin.set_token_tools(ctx, as_narrow, small.info.id, tool_mode="follow_role")


def test_agents_never_get_or_mint_tokens(ctx: AppContext, root: User) -> None:
    bot_admin, token = admin.create_user(ctx, root, "botadm", "admin", "agent")
    assert token is not None  # a human admin created it, so a token was issued
    acting = authenticate(ctx, token)
    with pytest.raises(PermissionDeniedError, match="agents never see tokens"):
        admin.create_token(ctx, acting, name="mine")
    with pytest.raises(PermissionDeniedError):
        admin.create_token(ctx, acting, "root", name="x")
    created, none = admin.create_user(ctx, acting, "helper", "viewer", "agent")
    assert none is None and created.kind is ActorKind.AGENT
    assert ctx.store.list_tokens("helper") == []
    plain, _ = admin.create_user(ctx, root, "plainbot", "contributor", "agent")
    with pytest.raises(PermissionDeniedError):
        admin.create_token(ctx, plain, name="x")
    events = ctx.audit.list_events(kind=AuditKind.ADMIN, limit=5)
    refused = [e for e in events if e.extra.get("op") == "create_token"]
    assert refused and all(e.outcome is AuditOutcome.REJECTED for e in refused)


def test_only_user_managers_manage_users(ctx: AppContext, root: User) -> None:
    approver, _ = admin.create_user(ctx, root, "ann", UserRole.APPROVER)
    with pytest.raises(PermissionDeniedError):
        admin.create_user(ctx, approver, "x", UserRole.VIEWER)
    with pytest.raises(PermissionDeniedError):
        admin.list_users(ctx, approver)
    with pytest.raises(PermissionDeniedError):
        admin.list_roles(ctx, approver)
    with pytest.raises(PermissionDeniedError):
        admin.set_role(ctx, approver, "ann", UserRole.ADMIN)
    rejected = ctx.audit.list_events(kind=AuditKind.ADMIN)[0]
    assert rejected.outcome is AuditOutcome.REJECTED and rejected.actor == "ann"
    views = admin.list_users(ctx, root)
    assert [u.name for u in views] == ["ann", "root"]
    assert [t.name for t in views[0].tokens] == ["default"]


def test_create_user_validation(ctx: AppContext, root: User) -> None:
    with pytest.raises(PolicyError):
        admin.create_user(ctx, root, "bad name", UserRole.VIEWER)
    with pytest.raises(PolicyError, match="role must be one of"):
        admin.create_user(ctx, root, "x", "superuser")
    with pytest.raises(PolicyError, match="kind"):
        admin.create_user(ctx, root, "x", "viewer", "robot")
    admin.create_user(ctx, root, "x", UserRole.VIEWER)
    with pytest.raises(StoreError):
        admin.create_user(ctx, root, "x", UserRole.VIEWER)


def test_set_role_active_and_last_admin_guard(ctx: AppContext, root: User) -> None:
    alice, _ = admin.create_user(ctx, root, "alice", UserRole.CONTRIBUTOR)
    assert admin.set_role(ctx, root, "alice", "approver").role == UserRole.APPROVER
    with pytest.raises(PolicyError, match="last admin"):
        admin.set_role(ctx, root, "root", UserRole.VIEWER)
    with pytest.raises(PolicyError, match="last admin"):
        admin.set_active(ctx, root, "root", False)
    admin.set_role(ctx, root, "alice", UserRole.ADMIN)
    assert admin.set_role(ctx, root, "root", UserRole.VIEWER).role == UserRole.VIEWER
    with pytest.raises(NotFoundError):
        admin.set_role(ctx, alice.model_copy(update={"role": UserRole.ADMIN}), "ghost", "viewer")
    with pytest.raises(PolicyError, match="role must be one of"):
        admin.set_role(ctx, root, "alice", "superuser")
    with pytest.raises(PolicyError):
        admin.update_user(ctx, root, "alice")


def test_deactivated_users_cannot_authenticate(ctx: AppContext, root: User) -> None:
    _, token = admin.create_user(ctx, root, "vic", UserRole.VIEWER)
    view = admin.set_active(ctx, root, "vic", False)
    assert view.active is False
    with pytest.raises(PermissionDeniedError, match="Invalid or missing token"):
        authenticate(ctx, token)
    with pytest.raises(PolicyError, match="deactivated"):
        admin.create_token(ctx, root, "vic", name="x")
    admin.set_active(ctx, root, "vic", True)
    assert authenticate(ctx, token).name == "vic"


def test_settings_admin_only(ctx: AppContext, root: User) -> None:
    viewer, _ = admin.create_user(ctx, root, "vic", UserRole.VIEWER)
    assert admin.get_settings(ctx, root).mode is TeamMode.OPEN
    assert admin.set_settings(ctx, root, "strict").mode is TeamMode.STRICT
    assert team_mode(ctx) is TeamMode.STRICT
    with pytest.raises(PermissionDeniedError):
        admin.set_settings(ctx, viewer, TeamMode.OPEN)
    with pytest.raises(PermissionDeniedError):
        admin.get_settings(ctx, viewer)
    with pytest.raises(PolicyError):
        admin.set_settings(ctx, root, "loose")
    assert team_mode(ctx) is TeamMode.STRICT


def test_agent_admin_limits(ctx: AppContext, root: User) -> None:
    """ADR 0027: an agent admin manages non-admin users and roles, never admins, the admin
    role, or tokens (beyond revoking non-admins')."""
    _, token = admin.create_user(ctx, root, "agentadm", "admin", "agent")
    bot = authenticate(ctx, token)
    # May create human and agent users (no token returned), with non-admin roles.
    hugo, none = admin.create_user(ctx, bot, "hugo", "viewer", "human")
    assert hugo.kind is ActorKind.HUMAN and none is None
    with pytest.raises(PermissionDeniedError, match="admin"):
        admin.create_user(ctx, bot, "boss", "admin", "human")
    assert ctx.store.get_user("boss") is None
    # Changes role / active of non-admins, but never grants admin.
    assert admin.set_role(ctx, bot, "hugo", "approver").role == UserRole.APPROVER
    with pytest.raises(PermissionDeniedError):
        admin.set_role(ctx, bot, "hugo", "admin")
    assert admin.set_active(ctx, bot, "hugo", False).active is False
    admin.set_active(ctx, bot, "hugo", True)
    # Never touches an admin user (a human admin, another agent admin, or itself).
    admin.create_user(ctx, root, "agentadm2", "admin", "agent")
    for target in ("root", "agentadm2", "agentadm"):
        with pytest.raises(PermissionDeniedError):
            admin.set_role(ctx, bot, target, "viewer")
        with pytest.raises(PermissionDeniedError):
            admin.set_active(ctx, bot, target, False)
    root_token = admin.list_tokens(ctx, root)[0]
    with pytest.raises(PermissionDeniedError):
        admin.revoke_token(ctx, bot, root_token.id, "root")
    # Revokes non-admin users' tokens, and its own.
    hugo_token = admin.create_token(ctx, root, "hugo", name="h")
    admin.revoke_token(ctx, bot, hugo_token.info.id, "hugo")
    with pytest.raises(PermissionDeniedError):
        authenticate(ctx, hugo_token.token)
    # Sets non-admin users' token tools; not admins'.
    hugo_default = admin.create_token(ctx, root, "hugo", name="d")
    admin.set_token_tools(
        ctx, bot, hugo_default.info.id, "hugo", tool_mode="only", include=["search"]
    )
    with pytest.raises(PermissionDeniedError):
        admin.set_token_tools(ctx, bot, root_token.id, "root", tool_mode="only")
    # Roles: create/edit others, never the admin role.
    admin.create_role(ctx, bot, "support_bot", allowed=["search", "compile_and_run"])
    admin.update_role(ctx, bot, "viewer", description="read only")
    with pytest.raises(PermissionDeniedError):
        admin.update_role(ctx, bot, "admin", defaults=["search"])
    own = admin.list_tokens(ctx, bot)[0]
    admin.revoke_token(ctx, bot, own.id)
    with pytest.raises(PermissionDeniedError):
        authenticate(ctx, token)
    assert ctx.store.get_user("root").role == UserRole.ADMIN


def test_roles_crud_and_rules(ctx: AppContext, root: User) -> None:
    card = admin.create_role(
        ctx,
        root,
        "support_bot",
        description=" search + run ",
        allowed=["search", "get_definition", "compile_and_run"],
        defaults=["search"],
    )
    assert card.description == "search + run" and card.defaults == [Tool.SEARCH]
    assert card.builtin is False and card.users == 0
    with pytest.raises(PolicyError, match="already exists"):
        admin.create_role(ctx, root, "support_bot", allowed=["search"])
    with pytest.raises(PolicyError, match="only the admin role"):
        admin.create_role(ctx, root, "sneaky", allowed=["search", "manage_user"])
    with pytest.raises(PolicyError, match="Invalid role"):
        admin.create_role(ctx, root, "Bad Name", allowed=["search"])
    with pytest.raises(PolicyError, match="defaults"):
        admin.create_role(ctx, root, "x", allowed=["search"], defaults=["verify"])
    # Shrinking allowed trims defaults; explicit defaults outside allowed are refused.
    shrunk = admin.update_role(ctx, root, "support_bot", allowed=["get_definition"])
    assert shrunk.allowed == [Tool.GET_DEFINITION] and shrunk.defaults == []
    with pytest.raises(PolicyError):
        admin.update_role(ctx, root, "support_bot", defaults=["search"])
    # The admin role: Allowed and review_all fixed; defaults editable by a human.
    with pytest.raises(PolicyError, match="every tool"):
        admin.update_role(ctx, root, "admin", allowed=["search"])
    with pytest.raises(PolicyError):
        admin.update_role(ctx, root, "admin", review_all=False)
    # Defaults must keep the admin tools, or every admin's follow-role token (and GUI
    # session) would lose user / role management at once.
    with pytest.raises(PolicyError, match="must keep the admin tools"):
        admin.update_role(ctx, root, "admin", defaults=["search", "list_users"])
    admin_tools = ["list_users", "manage_user", "manage_role", "manage_settings"]
    edited = admin.update_role(ctx, root, "admin", defaults=["search", *admin_tools])
    assert edited.allowed == list(Tool)
    assert set(edited.defaults) == {Tool.SEARCH, *(Tool(t) for t in admin_tools)}
    # Delete: custom roles nobody holds.
    admin.create_user(ctx, root, "sb", "support_bot", "agent")
    with pytest.raises(PolicyError, match="held by users"):
        admin.delete_role(ctx, root, "support_bot")
    admin.set_role(ctx, root, "sb", "viewer")
    admin.delete_role(ctx, root, "support_bot")
    with pytest.raises(NotFoundError):
        admin.delete_role(ctx, root, "support_bot")
    with pytest.raises(PolicyError, match="built-in"):
        admin.delete_role(ctx, root, "viewer")
    with pytest.raises(NotFoundError):
        admin.update_role(ctx, root, "ghost", description="x")
    viewer, _ = admin.create_user(ctx, root, "vic", "viewer")
    with pytest.raises(PermissionDeniedError):
        admin.create_role(ctx, viewer, "y", allowed=["search"])
    ops = [e.extra["op"] for e in ctx.audit.list_events(kind=AuditKind.ADMIN, limit=500)]
    assert {"create_role", "update_role", "delete_role"} <= set(ops)


def test_role_changes_apply_to_tokens_on_the_next_call(ctx: AppContext, root: User) -> None:
    admin.create_role(
        ctx, root, "bot_role", allowed=["search", "check_and_run"], defaults=["search"]
    )
    _, token = admin.create_user(ctx, root, "bot", "bot_role", "agent")
    assert authenticate(ctx, token).tools == {Tool.SEARCH}
    admin.update_role(ctx, root, "bot_role", defaults=["search", "check_and_run"])
    assert authenticate(ctx, token).tools == {Tool.SEARCH, Tool.CHECK_AND_RUN}
    admin.set_role(ctx, root, "bot", "viewer")
    assert Tool.LIST_PROPOSALS in authenticate(ctx, token).tools


def test_me_reports_effective_tools_and_role_limits(ctx: AppContext, root: User) -> None:
    admin.create_role(ctx, root, "bot_role", allowed=["search", "verify"], defaults=["search"])
    _, token = admin.create_user(ctx, root, "bot", "bot_role", "agent")
    card = admin.me(ctx, authenticate(ctx, token))
    assert card.tools == [Tool.SEARCH] and card.review_all is False
    assert card.role_allowed == [Tool.SEARCH]  # verify ignored for an agent
    assert card.role_defaults == [Tool.SEARCH]
    root_card = admin.me(ctx, root)
    assert root_card.tools == list(Tool) and root_card.review_all is True


def test_default_connection_admin_only_validated_and_audited() -> None:
    profiles = [
        WarehouseProfile(name="pg", dialect=Dialect.POSTGRES),
        WarehouseProfile(name="sf", dialect=Dialect.SNOWFLAKE),
    ]
    with open_context(AppConfig(db_url="sqlite://"), profiles=profiles) as c:
        admin_user, _ = admin.bootstrap_admin(c, "root")
        viewer, _ = admin.create_user(c, admin_user, "vic", "viewer")
        unset = admin.get_settings(c, admin_user)
        assert (unset.default_connection, unset.effective_default_connection) == (None, None)
        saved = admin.set_default_connection(c, admin_user, " sf ")
        assert (saved.default_connection, saved.effective_default_connection) == ("sf", "sf")
        assert saved.mode is TeamMode.OPEN and saved.issues == []
        assert c.warehouses.summary().dialect is Dialect.SNOWFLAKE
        event = c.audit.list_events(kind=AuditKind.ADMIN, limit=1)[0]
        assert event.extra["op"] == "set_default_connection"
        assert event.extra["default_connection"] == "sf"
        with pytest.raises(NotFoundError, match="'nope' is not configured"):
            admin.set_default_connection(c, admin_user, "nope")
        assert c.audit.list_events(kind=AuditKind.ADMIN, limit=1)[0].outcome is (AuditOutcome.ERROR)
        with pytest.raises(PermissionDeniedError):
            admin.set_default_connection(c, viewer, "pg")
        assert admin.get_settings(c, admin_user).default_connection == "sf"
        # A stored profile later removed from connections.yml: a warning, not a failure.
        c.store.set_setting(DEFAULT_CONNECTION_SETTING, "gone")
        stale = admin.get_settings(c, admin_user)
        assert stale.default_connection == "gone" and stale.effective_default_connection is None
        assert [i.code for i in stale.issues] == [IssueCode.DEFAULT_CONNECTION_MISSING]
        cleared = admin.set_default_connection(c, admin_user, None)
        assert cleared.default_connection is None and cleared.issues == []
        assert c.store.get_setting(DEFAULT_CONNECTION_SETTING) is None
