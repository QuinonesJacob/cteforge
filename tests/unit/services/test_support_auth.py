from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Iterator

import pytest

from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import PermissionDeniedError
from cteforge.domain.models import Definition, DefinitionState
from cteforge.domain.users import ActorKind, User, UserRole
from cteforge.services.auth import (
    authenticate,
    hash_token,
    local_user,
    new_token,
    permitted,
    require,
    resolve_user,
)
from cteforge.services.context import AppConfig, AppContext, AuthMode, open_context
from cteforge.trust.approval import Action

FIXED = dt.datetime(2026, 11, 16, 9, 30, tzinfo=dt.UTC)


@pytest.fixture
def ctx() -> Iterator[AppContext]:
    with open_context(AppConfig(db_url="sqlite://", user="jq"), clock=lambda: FIXED) as c:
        yield c


def _user(role: UserRole, kind: ActorKind = ActorKind.HUMAN, name: str = "u") -> User:
    return User(name=name, role=role, kind=kind)


def _definition(state: DefinitionState, owner: str | None = None) -> Definition:
    return Definition(
        id="rev",
        title="Revenue",
        source_dialect=Dialect.DUCKDB,
        template="SELECT 1 AS x",
        state=state,
        owner=owner,
    )


def test_hash_token_is_sha256_hex() -> None:
    assert hash_token("abc") == hashlib.sha256(b"abc").hexdigest()
    assert len(hash_token("abc")) == 64


def test_new_token_is_random_and_long() -> None:
    tokens = {new_token() for _ in range(20)}
    assert len(tokens) == 20
    assert all(len(t) >= 43 for t in tokens)


def test_authenticate_maps_token_to_user(ctx: AppContext) -> None:
    token = new_token()
    ctx.store.add_user(_user(UserRole.CONTRIBUTOR, ActorKind.AGENT, "bot"), hash_token(token))
    user = authenticate(ctx, token)
    assert user.name == "bot" and user.kind is ActorKind.AGENT


@pytest.mark.parametrize("token", [None, "", "wrong-token", "x" * 10_000])
def test_authenticate_failures_are_generic(ctx: AppContext, token: str | None) -> None:
    ctx.store.add_user(_user(UserRole.ADMIN, name="admin"), hash_token(new_token()))
    with pytest.raises(PermissionDeniedError) as info:
        authenticate(ctx, token)
    assert str(info.value) == "Invalid or missing token"


def test_local_user_created_once_as_human_admin(ctx: AppContext) -> None:
    first = local_user(ctx)
    assert (first.name, first.role, first.kind) == ("jq", UserRole.ADMIN, ActorKind.HUMAN)
    assert first.created_at == FIXED
    assert local_user(ctx) == first
    assert [u.name for u in ctx.store.list_users()] == ["jq"]


def test_local_user_keeps_existing_role(ctx: AppContext) -> None:
    ctx.store.add_user(_user(UserRole.VIEWER, name="jq"), hash_token(new_token()))
    assert local_user(ctx).role is UserRole.VIEWER


def test_resolve_user_depends_on_auth_mode() -> None:
    with open_context(AppConfig(db_url="sqlite://", auth=AuthMode.OFF, user="me")) as off:
        assert resolve_user(off, None).name == "me"
    with open_context(AppConfig(db_url="sqlite://")) as on:
        with pytest.raises(PermissionDeniedError):
            resolve_user(on, None)
        token = new_token()
        on.store.add_user(_user(UserRole.VIEWER, name="v"), hash_token(token))
        assert resolve_user(on, token).name == "v"


@pytest.mark.parametrize(
    ("role", "action", "ok"),
    [
        (UserRole.VIEWER, Action.SEARCH, True),
        (UserRole.VIEWER, Action.RUN, True),
        (UserRole.VIEWER, Action.PROPOSE, False),
        (UserRole.CONTRIBUTOR, Action.PROPOSE, True),
        (UserRole.CONTRIBUTOR, Action.APPROVE, False),
        (UserRole.APPROVER, Action.APPROVE, True),
        (UserRole.APPROVER, Action.MANAGE_USERS, False),
        (UserRole.ADMIN, Action.MANAGE_SETTINGS, True),
    ],
)
def test_require_role_minimums(role: UserRole, action: Action, ok: bool) -> None:
    user = _user(role)
    assert permitted(user, action) is ok
    if ok:
        require(user, action)
    else:
        with pytest.raises(PermissionDeniedError, match=action.value):
            require(user, action)


def test_agents_never_verify() -> None:
    agent = _user(UserRole.ADMIN, ActorKind.AGENT)
    with pytest.raises(PermissionDeniedError):
        require(agent, Action.VERIFY, _definition(DefinitionState.LLM_ANNOTATED))
    require(_user(UserRole.APPROVER), Action.VERIFY, _definition(DefinitionState.LLM_ANNOTATED))


def test_owner_may_approve_but_not_verify() -> None:
    owner = _user(UserRole.CONTRIBUTOR, name="alice")
    target = _definition(DefinitionState.VERIFIED, owner="alice")
    require(owner, Action.APPROVE, target)
    with pytest.raises(PermissionDeniedError, match="'rev'"):
        require(owner, Action.VERIFY, target)
    with pytest.raises(PermissionDeniedError):
        require(_user(UserRole.CONTRIBUTOR, name="bob"), Action.APPROVE, target)


def test_agent_cannot_approve_changes_to_verified() -> None:
    agent = _user(UserRole.APPROVER, ActorKind.AGENT)
    with pytest.raises(PermissionDeniedError):
        require(agent, Action.APPROVE, _definition(DefinitionState.VERIFIED))
    require(agent, Action.APPROVE, _definition(DefinitionState.LLM_ANNOTATED))


def test_local_user_is_not_created_in_a_store_with_users(ctx: AppContext) -> None:
    """Regression: any unknown CTEFORGE_USER became a HUMAN ADMIN even in a team store."""
    ctx.store.add_user(_user(UserRole.VIEWER, name="alice"), hash_token(new_token()))
    with pytest.raises(PermissionDeniedError, match="does not exist"):
        local_user(ctx)
    assert [u.name for u in ctx.store.list_users()] == ["alice"]


def test_local_user_uses_a_stored_agent_as_is(ctx: AppContext) -> None:
    ctx.store.add_user(
        _user(UserRole.CONTRIBUTOR, ActorKind.AGENT, name="jq"), hash_token(new_token())
    )
    user = local_user(ctx)
    assert user.kind is ActorKind.AGENT and not permitted(user, Action.VERIFY)
