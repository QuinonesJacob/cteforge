"""Users, roles, tokens and team settings (ADR 0018, DESIGN §8)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

import pytest
from sqlalchemy import select

from cteforge.domain.audit import AuditKind, AuditOutcome
from cteforge.domain.errors import (
    NotFoundError,
    PermissionDeniedError,
    PolicyError,
    StoreError,
)
from cteforge.domain.models import TeamMode
from cteforge.domain.users import ActorKind, User, UserRole
from cteforge.services import admin
from cteforge.services.auth import authenticate, hash_token
from cteforge.services.context import AppConfig, AppContext, open_context, team_mode
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


def _stored_hashes(ctx: AppContext) -> list[str]:
    with ctx.store._engine.connect() as conn:  # test-only peek at the raw column
        return list(conn.execute(select(tables.users.c.token_sha256)).scalars())


def test_bootstrap_only_while_no_users(ctx: AppContext) -> None:
    user, token = admin.bootstrap_admin(ctx, "root")
    assert user.role is UserRole.ADMIN and user.kind is ActorKind.HUMAN
    assert authenticate(ctx, token).name == "root"
    with pytest.raises(PolicyError):
        admin.bootstrap_admin(ctx, "second")
    event = ctx.audit.list_events(kind=AuditKind.ADMIN)[0]
    assert event.actor == "root" and event.extra["op"] == "bootstrap_admin"


def test_create_user_token_lifecycle(ctx: AppContext, root: User) -> None:
    bot, token = admin.create_user(ctx, root, "claude-agent", "contributor", "agent")
    assert bot.role is UserRole.CONTRIBUTOR and bot.kind is ActorKind.AGENT
    assert bot.created_at == FIXED
    assert authenticate(ctx, token) == ctx.store.get_user("claude-agent")
    hashes = _stored_hashes(ctx)
    assert token not in hashes and hash_token(token) in hashes  # never stored raw
    new = admin.rotate_token(ctx, root, "claude-agent")
    assert new != token and authenticate(ctx, new).name == "claude-agent"
    with pytest.raises(PermissionDeniedError):
        authenticate(ctx, token)  # old token stops working
    for event in ctx.audit.list_events(kind=AuditKind.ADMIN):
        assert token not in event.model_dump_json() and new not in event.model_dump_json()


def test_users_may_rotate_only_their_own_token(ctx: AppContext, root: User) -> None:
    alice, _ = admin.create_user(ctx, root, "alice", UserRole.CONTRIBUTOR)
    admin.create_user(ctx, root, "bob", UserRole.CONTRIBUTOR)
    mine = admin.rotate_token(ctx, alice, "alice")
    assert authenticate(ctx, mine).name == "alice"
    with pytest.raises(PermissionDeniedError):
        admin.rotate_token(ctx, alice, "bob")
    with pytest.raises(NotFoundError):
        admin.rotate_token(ctx, root, "nobody")


def test_only_admins_manage_users(ctx: AppContext, root: User) -> None:
    approver, _ = admin.create_user(ctx, root, "ann", UserRole.APPROVER)
    with pytest.raises(PermissionDeniedError):
        admin.create_user(ctx, approver, "x", UserRole.VIEWER)
    with pytest.raises(PermissionDeniedError):
        admin.list_users(ctx, approver)
    with pytest.raises(PermissionDeniedError):
        admin.set_role(ctx, approver, "ann", UserRole.ADMIN)
    rejected = ctx.audit.list_events(kind=AuditKind.ADMIN)[0]
    assert rejected.outcome is AuditOutcome.REJECTED and rejected.actor == "ann"
    assert [u.name for u in admin.list_users(ctx, root)] == ["ann", "root"]


def test_create_user_validation(ctx: AppContext, root: User) -> None:
    with pytest.raises(PolicyError):
        admin.create_user(ctx, root, "bad name", UserRole.VIEWER)
    with pytest.raises(PolicyError):
        admin.create_user(ctx, root, "x", "superuser")
    admin.create_user(ctx, root, "x", UserRole.VIEWER)
    with pytest.raises(StoreError):
        admin.create_user(ctx, root, "x", UserRole.VIEWER)


def test_set_role_and_last_admin_guard(ctx: AppContext, root: User) -> None:
    alice, _ = admin.create_user(ctx, root, "alice", UserRole.CONTRIBUTOR)
    assert admin.set_role(ctx, root, "alice", "approver").role is UserRole.APPROVER
    with pytest.raises(PolicyError, match="last admin"):
        admin.set_role(ctx, root, "root", UserRole.VIEWER)
    admin.set_role(ctx, root, "alice", UserRole.ADMIN)
    assert admin.set_role(ctx, root, "root", UserRole.VIEWER).role is UserRole.VIEWER
    with pytest.raises(NotFoundError):
        admin.set_role(ctx, alice.model_copy(update={"role": UserRole.ADMIN}), "ghost", "viewer")


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


def test_agent_admin_cannot_mint_or_take_over_humans(ctx: AppContext, root: User) -> None:
    """Regression: an agent admin could create a human approver (or rotate a human's
    token) and then act as that human to verify (ADR 0007)."""
    agent_admin, _ = admin.create_user(ctx, root, "agentadm", "admin", "agent")
    with pytest.raises(PermissionDeniedError):
        admin.create_user(ctx, agent_admin, "puppet", "approver", "human")
    assert ctx.store.get_user("puppet") is None
    human, token = admin.create_user(ctx, root, "hugo", "viewer", "human")
    with pytest.raises(PermissionDeniedError):
        admin.rotate_token(ctx, agent_admin, "hugo")
    assert authenticate(ctx, token).name == "hugo"  # old token still valid
    with pytest.raises(PermissionDeniedError):
        admin.set_role(ctx, agent_admin, "hugo", "approver")
    assert ctx.store.get_user("hugo").role is UserRole.VIEWER
    # Agent users stay manageable by an agent admin.
    bot, _ = admin.create_user(ctx, agent_admin, "bot2", "contributor", "agent")
    assert bot.kind is ActorKind.AGENT
    assert admin.rotate_token(ctx, agent_admin, "bot2")
    assert admin.set_role(ctx, agent_admin, "bot2", "viewer").role is UserRole.VIEWER
    assert admin.rotate_token(ctx, agent_admin, "agentadm")  # own token
    event = ctx.audit.list_events(kind=AuditKind.ADMIN, limit=1)[0]
    assert event.actor == "agentadm" and event.outcome is AuditOutcome.OK
