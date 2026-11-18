from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Iterator

import pytest
from pydantic import SecretStr

from cteforge.domain.access import Role, TokenInfo, Tool, ToolMode
from cteforge.domain.audit import AuditOutcome
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import PermissionDeniedError
from cteforge.domain.models import Definition, DefinitionState
from cteforge.domain.users import ActorKind, User, UserRole
from cteforge.services.auth import (
    LAST_USED_INTERVAL,
    authenticate,
    hash_token,
    local_user,
    new_token,
    permitted,
    refuse_tool,
    require,
    resolve_user,
    stdio_user,
    user_tools,
)
from cteforge.services.context import AppConfig, AppContext, AuthMode, open_context

FIXED = dt.datetime(2026, 11, 16, 9, 30, tzinfo=dt.UTC)


class Clock:
    def __init__(self) -> None:
        self.now = FIXED

    def __call__(self) -> dt.datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def ctx(clock: Clock) -> Iterator[AppContext]:
    with open_context(AppConfig(db_url="sqlite://", user="jq"), clock=clock) as c:
        yield c


def _user(role: str, kind: ActorKind = ActorKind.HUMAN, name: str = "u") -> User:
    return User(name=name, role=role, kind=kind)


def add(
    ctx: AppContext,
    user: User,
    *,
    mode: ToolMode = ToolMode.FOLLOW_ROLE,
    include: frozenset[Tool] = frozenset(),
    exclude: frozenset[Tool] = frozenset(),
    expires_at: dt.datetime | None = None,
    token_id: str | None = None,
) -> str:
    """Store `user` (if new) and a token for it; return the token."""
    if ctx.store.get_user(user.name) is None:
        ctx.store.add_user(user)
    token = new_token()
    ctx.store.add_token(
        TokenInfo(
            id=token_id or f"tk-{user.name}-{len(ctx.store.list_tokens(user.name))}",
            user_name=user.name,
            name="test",
            prefix=token[:12],
            created_at=ctx.now(),
            created_by=user.name,
            expires_at=expires_at,
            tool_mode=mode,
            include=include,
            exclude=exclude,
        ),
        hash_token(token),
    )
    return token


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


def test_new_token_is_a_prefixed_random_pat() -> None:
    tokens = {new_token() for _ in range(20)}
    assert len(tokens) == 20
    assert all(t.startswith("cfg_pat_") and len(t) == len("cfg_pat_") + 43 for t in tokens)


def test_authenticate_maps_token_to_user_with_its_tools(ctx: AppContext) -> None:
    token = add(ctx, _user(UserRole.CONTRIBUTOR, ActorKind.AGENT, "bot"))
    user = authenticate(ctx, token)
    assert user.name == "bot" and user.kind is ActorKind.AGENT
    assert Tool.PROPOSE_DEFINITION in user.tools and Tool.VERIFY not in user.tools
    assert user.review_all is False


def test_token_tools_narrow_the_role(ctx: AppContext) -> None:
    only = add(ctx, _user(UserRole.ADMIN, name="root"), mode=ToolMode.ONLY, include={Tool.SEARCH})
    user = authenticate(ctx, only)
    assert user.tools == {Tool.SEARCH}
    assert permitted(user, Tool.SEARCH) and not permitted(user, Tool.MANAGE_USER)
    minus = add(ctx, _user(UserRole.ADMIN, name="root"), exclude={Tool.CHECK_AND_RUN})
    assert Tool.CHECK_AND_RUN not in authenticate(ctx, minus).tools


def test_token_never_exceeds_the_role(ctx: AppContext) -> None:
    token = add(
        ctx,
        _user(UserRole.VIEWER, name="vic"),
        mode=ToolMode.ONLY,
        include={Tool.SEARCH, Tool.MANAGE_USER, Tool.VERIFY},
    )
    assert authenticate(ctx, token).tools == {Tool.SEARCH}


def test_custom_role_tools_and_verify_for_agents(ctx: AppContext) -> None:
    ctx.store.add_role(
        Role(
            name="qa",
            allowed=frozenset({Tool.SEARCH, Tool.VERIFY}),
            defaults=frozenset({Tool.SEARCH, Tool.VERIFY}),
        )
    )
    human = authenticate(ctx, add(ctx, _user("qa", name="hq")))
    agent = authenticate(ctx, add(ctx, _user("qa", ActorKind.AGENT, name="aq")))
    assert human.tools == {Tool.SEARCH, Tool.VERIFY}
    assert agent.tools == {Tool.SEARCH}  # verify ignored for agents outside admin


@pytest.mark.parametrize("token", [None, "", "wrong-token", "cfg_pat_" + "x" * 43, "x" * 10_000])
def test_authenticate_failures_are_generic(ctx: AppContext, token: str | None) -> None:
    add(ctx, _user(UserRole.ADMIN, name="admin"))
    with pytest.raises(PermissionDeniedError) as info:
        authenticate(ctx, token)
    assert str(info.value) == "Invalid or missing token"


def test_revoked_expired_and_deactivated_fail_the_same_way(ctx: AppContext, clock: Clock) -> None:
    alice = _user(UserRole.VIEWER, name="alice")
    revoked = add(ctx, alice, token_id="r")
    expiring = add(ctx, alice, token_id="e", expires_at=FIXED + dt.timedelta(hours=1))
    good = add(ctx, alice, token_id="g")
    authenticate(ctx, revoked)
    ctx.store.revoke_token("r", FIXED)
    assert authenticate(ctx, expiring).name == "alice"
    clock.now = FIXED + dt.timedelta(hours=1)
    messages = set()
    for token in (revoked, expiring):
        with pytest.raises(PermissionDeniedError) as info:
            authenticate(ctx, token)
        messages.add(str(info.value))
    assert authenticate(ctx, good).name == "alice"
    ctx.store.set_user_active("alice", False)
    with pytest.raises(PermissionDeniedError) as info:
        authenticate(ctx, good)
    messages.add(str(info.value))
    assert messages == {"Invalid or missing token"}
    ctx.store.set_user_active("alice", True)
    assert authenticate(ctx, good).name == "alice"


def test_last_used_is_written_at_most_once_a_minute(ctx: AppContext, clock: Clock) -> None:
    token = add(ctx, _user(UserRole.VIEWER, name="v"), token_id="t")
    authenticate(ctx, token)
    assert ctx.store.get_token("t").last_used_at == FIXED
    clock.now = FIXED + dt.timedelta(seconds=30)
    authenticate(ctx, token)
    assert ctx.store.get_token("t").last_used_at == FIXED
    clock.now = FIXED + LAST_USED_INTERVAL
    authenticate(ctx, token)
    assert ctx.store.get_token("t").last_used_at == FIXED + LAST_USED_INTERVAL


def test_local_user_created_once_as_human_admin_with_every_tool(ctx: AppContext) -> None:
    first = local_user(ctx)
    assert (first.name, first.role, first.kind) == ("jq", UserRole.ADMIN, ActorKind.HUMAN)
    assert first.created_at == FIXED
    assert first.tools == frozenset(Tool) and first.review_all is True
    assert local_user(ctx) == first
    assert [u.name for u in ctx.store.list_users()] == ["jq"]
    assert ctx.store.list_tokens("jq") == []  # no token: not usable over HTTP


def test_local_user_keeps_existing_role(ctx: AppContext) -> None:
    ctx.store.add_user(_user(UserRole.VIEWER, name="jq"))
    user = local_user(ctx)
    assert user.role == UserRole.VIEWER and Tool.PROPOSE_DEFINITION not in user.tools


def test_local_user_role_allowed_vs_defaults(ctx: AppContext) -> None:
    ctx.store.add_role(
        Role(
            name="narrow",
            allowed=frozenset({Tool.SEARCH, Tool.CHECK_AND_RUN}),
            defaults=frozenset({Tool.SEARCH}),
        )
    )
    ctx.store.add_user(_user("narrow", ActorKind.AGENT, name="jq"))
    assert local_user(ctx).tools == {Tool.SEARCH, Tool.CHECK_AND_RUN}  # CLI: allowed
    assert local_user(ctx, create=False, defaults_only=True).tools == {Tool.SEARCH}
    assert stdio_user(ctx).tools == {Tool.SEARCH}  # stdio MCP without a token: defaults


def test_deactivated_local_user_is_refused(ctx: AppContext) -> None:
    ctx.store.add_user(User(name="jq", role=UserRole.ADMIN, active=False))
    with pytest.raises(PermissionDeniedError, match="deactivated"):
        local_user(ctx)


def test_stdio_user_prefers_cteforge_token() -> None:
    with open_context(AppConfig(db_url="sqlite://", user="nobody")) as c:
        token = add(
            c, _user(UserRole.ADMIN, name="root"), mode=ToolMode.ONLY, include={Tool.SEARCH}
        )
        c.config = c.config.model_copy(update={"token": SecretStr(token)})
        user = stdio_user(c)
        assert user.name == "root" and user.tools == {Tool.SEARCH}
        assert token not in repr(c.config)  # SecretStr: never printed
        c.config = c.config.model_copy(update={"token": SecretStr("cfg_pat_bad")})
        with pytest.raises(PermissionDeniedError, match="Invalid or missing token"):
            stdio_user(c)


def test_cteforge_token_is_read_from_the_environment() -> None:
    config = AppConfig.from_env({"CTEFORGE_TOKEN": "cfg_pat_secret"})
    assert config.token is not None and config.token.get_secret_value() == "cfg_pat_secret"
    assert "cfg_pat_secret" not in repr(config)
    assert AppConfig.from_env({}).token is None


def test_resolve_user_depends_on_auth_mode() -> None:
    with open_context(AppConfig(db_url="sqlite://", auth=AuthMode.OFF, user="me")) as off:
        assert resolve_user(off, None).name == "me"
    with open_context(AppConfig(db_url="sqlite://")) as on:
        with pytest.raises(PermissionDeniedError):
            resolve_user(on, None)
        token = add(on, _user(UserRole.VIEWER, name="v"))
        assert resolve_user(on, token).name == "v"


@pytest.mark.parametrize(
    ("role", "tool", "ok"),
    [
        (UserRole.VIEWER, Tool.SEARCH, True),
        (UserRole.VIEWER, Tool.COMPILE_AND_RUN, True),
        (UserRole.VIEWER, Tool.LIST_PROPOSALS, True),
        (UserRole.VIEWER, Tool.PARSE_QUERY, False),
        (UserRole.VIEWER, Tool.PROPOSE_DEFINITION, False),
        (UserRole.CONTRIBUTOR, Tool.PROPOSE_DEFINITION, True),
        (UserRole.CONTRIBUTOR, Tool.VIEW_AUDIT, False),
        (UserRole.APPROVER, Tool.VIEW_AUDIT, True),
        (UserRole.APPROVER, Tool.MANAGE_USER, False),
        (UserRole.ADMIN, Tool.MANAGE_SETTINGS, True),
    ],
)
def test_require_tools(role: UserRole, tool: Tool, ok: bool) -> None:
    user = _user(role)
    assert permitted(user, tool) is ok
    if ok:
        require(user, tool)
    else:
        with pytest.raises(PermissionDeniedError, match=tool.value):
            require(user, tool)


def test_only_admin_agents_verify() -> None:
    llm = _definition(DefinitionState.LLM_ANNOTATED)
    with pytest.raises(PermissionDeniedError):
        require(_user(UserRole.APPROVER, ActorKind.AGENT), Tool.VERIFY, llm)
    require(_user(UserRole.ADMIN, ActorKind.AGENT), Tool.VERIFY, llm)
    require(_user(UserRole.APPROVER), Tool.VERIFY, llm)


def test_owner_may_approve_but_not_verify() -> None:
    owner = _user(UserRole.CONTRIBUTOR, name="alice")
    target = _definition(DefinitionState.VERIFIED, owner="alice")
    require(owner, Tool.REVIEW_PROPOSAL, target)
    with pytest.raises(PermissionDeniedError, match="'rev'"):
        require(owner, Tool.VERIFY, target)
    with pytest.raises(PermissionDeniedError):
        require(_user(UserRole.CONTRIBUTOR, name="bob"), Tool.REVIEW_PROPOSAL, target)


def test_non_admin_agent_cannot_approve_changes_to_verified() -> None:
    agent = _user(UserRole.APPROVER, ActorKind.AGENT)
    with pytest.raises(PermissionDeniedError):
        require(agent, Tool.REVIEW_PROPOSAL, _definition(DefinitionState.VERIFIED))
    require(agent, Tool.REVIEW_PROPOSAL, _definition(DefinitionState.LLM_ANNOTATED))
    admin_agent = _user(UserRole.ADMIN, ActorKind.AGENT)
    require(admin_agent, Tool.REVIEW_PROPOSAL, _definition(DefinitionState.VERIFIED))


def test_local_user_is_not_created_in_a_store_with_users(ctx: AppContext) -> None:
    """Regression: any unknown CTEFORGE_USER became a HUMAN ADMIN even in a team store."""
    ctx.store.add_user(_user(UserRole.VIEWER, name="alice"))
    with pytest.raises(PermissionDeniedError, match="does not exist"):
        local_user(ctx)
    assert [u.name for u in ctx.store.list_users()] == ["alice"]


def test_local_user_uses_a_stored_agent_as_is(ctx: AppContext) -> None:
    ctx.store.add_user(_user(UserRole.CONTRIBUTOR, ActorKind.AGENT, name="jq"))
    user = local_user(ctx)
    assert user.kind is ActorKind.AGENT and not permitted(user, Tool.VERIFY)


def test_local_user_without_create_never_creates_anyone(ctx: AppContext) -> None:
    """stdio MCP: the client (usually an agent) must not become the first human admin."""
    with pytest.raises(PermissionDeniedError, match="user add"):
        local_user(ctx, create=False)
    assert ctx.store.list_users() == []
    made = local_user(ctx)  # a person's first local use (CLI, auth-off GUI) still works
    assert local_user(ctx, create=False) == made


def test_user_tools_and_refuse_tool_audit(ctx: AppContext) -> None:
    bot = authenticate(
        ctx,
        add(
            ctx,
            _user(UserRole.VIEWER, ActorKind.AGENT, "bot"),
            mode=ToolMode.ONLY,
            include={Tool.SEARCH},
        ),
    )
    assert user_tools(bot) == {Tool.SEARCH}
    assert user_tools(bot.model_copy(update={"active": False})) == frozenset()
    error = refuse_tool(ctx, bot, "manage_user")
    assert isinstance(error, PermissionDeniedError) and "manage_user" in str(error)
    [event] = ctx.audit.list_events(limit=1)
    assert event.actor == "bot" and event.outcome is AuditOutcome.REJECTED
    assert event.extra == {"op": "tool_refused", "tool": "manage_user"}
