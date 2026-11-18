import pytest

from cteforge.domain.access import (
    ADMIN_ONLY_TOOLS,
    ALL_TOOLS,
    BUILTIN_ROLES,
    TOOL_GROUPS,
    Role,
    TokenInfo,
    Tool,
    ToolGroup,
    ToolMode,
)
from cteforge.domain.dialects import Dialect
from cteforge.domain.issues import Issue, IssueCode, Severity
from cteforge.domain.models import Definition, DefinitionState, TeamMode
from cteforge.domain.users import ActorKind, User, UserRole
from cteforge.trust import allowed, can_approve, can_auto_apply, can_verify, tools_of
from cteforge.trust.approval import (
    can_edit_role,
    can_grant_role,
    can_manage_user,
    can_mint_token,
    can_revoke_token,
    can_set_token_tools,
    check_role,
    effective_tools,
    role_ceiling,
    role_tools,
)

HUMAN = ActorKind.HUMAN
AGENT = ActorKind.AGENT
VIEWER, CONTRIB, APPROVER, ADMIN = (
    UserRole.VIEWER,
    UserRole.CONTRIBUTOR,
    UserRole.APPROVER,
    UserRole.ADMIN,
)


def user(role: UserRole, kind: ActorKind = HUMAN, name: str = "someone") -> User:
    return User(name=name, role=role, kind=kind)


def definition(state: DefinitionState, owner: str | None = "owner") -> Definition:
    return Definition(
        id="closed_deals",
        title="Closed deals",
        source_dialect=Dialect.DUCKDB,
        template="SELECT 1",
        owner=owner,
        state=state,
    )


NEW = None
DRAFT = definition(DefinitionState.DRAFT)
LLM = definition(DefinitionState.LLM_ANNOTATED)
VERIFIED = definition(DefinitionState.VERIFIED)
UNOWNED_VERIFIED = definition(DefinitionState.VERIFIED, owner=None)

# Built-in role → tools (ADR 0027 addendum: today's behaviour as tools).
USE = set(TOOL_GROUPS[ToolGroup.USE])
BUILD = set(TOOL_GROUPS[ToolGroup.BUILD])
EXPECTED_TOOLS = {
    VIEWER: USE | {Tool.LIST_PROPOSALS},
    CONTRIB: USE | {Tool.LIST_PROPOSALS} | BUILD | {Tool.REVIEW_PROPOSAL},
    APPROVER: USE
    | BUILD
    | {Tool.LIST_PROPOSALS, Tool.REVIEW_PROPOSAL, Tool.VERIFY, Tool.VIEW_AUDIT},
    ADMIN: set(Tool),
}
ORDER = [VIEWER, CONTRIB, APPROVER, ADMIN]


def test_builtin_roles_match_todays_behaviour():
    for role in ORDER:
        assert set(BUILTIN_ROLES[role].allowed) == EXPECTED_TOOLS[role]
        assert BUILTIN_ROLES[role].defaults == BUILTIN_ROLES[role].allowed
    assert [BUILTIN_ROLES[r].review_all for r in ORDER] == [False, False, True, True]
    assert all(check_role(BUILTIN_ROLES[r]) == [] for r in ORDER)


@pytest.mark.parametrize("role", ORDER)
@pytest.mark.parametrize("tool", list(Tool))
def test_allowed_for_humans_follows_the_builtin_role(tool, role):
    assert allowed(user(role), tool) is (tool in EXPECTED_TOOLS[role])


@pytest.mark.parametrize("role", ORDER)
@pytest.mark.parametrize("tool", list(Tool))
def test_agents_same_as_humans_except_verify_outside_admin(tool, role):
    expected = tool in EXPECTED_TOOLS[role]
    if tool is Tool.VERIFY and role is not ADMIN:
        expected = False
    assert allowed(user(role, AGENT), tool) is expected


def test_resolved_tools_replace_the_role_fallback():
    narrow = User(name="bot", role=ADMIN, kind=AGENT, tools=frozenset({Tool.SEARCH}))
    assert allowed(narrow, Tool.SEARCH)
    assert not allowed(narrow, Tool.MANAGE_USER)
    assert tools_of(narrow) == {Tool.SEARCH}
    # A role cteforge does not ship and no resolved tools: nothing.
    assert tools_of(User(name="x", role="support_bot")) == frozenset()
    assert not allowed(User(name="x", role="support_bot"), Tool.SEARCH)


def test_resolved_verify_is_still_refused_to_a_non_admin_agent():
    forged = User(name="bot", role=APPROVER, kind=AGENT, tools=frozenset({Tool.VERIFY}))
    assert not allowed(forged, Tool.VERIFY)
    assert not can_verify(forged, None)


def test_deactivated_users_hold_nothing():
    gone = User(name="ann", role=ADMIN, active=False)
    assert not any(allowed(gone, t) for t in Tool)
    assert not can_approve(gone, None) and not can_verify(gone, None)
    assert not can_mint_token(gone, gone) and not can_revoke_token(gone, gone)


# (id, user, target, can_approve, can_verify)
APPROVAL_CASES = [
    # Role-based
    ("viewer_new", user(VIEWER), NEW, False, False),
    ("contrib_new", user(CONTRIB), NEW, False, False),
    ("approver_new", user(APPROVER), NEW, True, True),
    ("admin_new", user(ADMIN), NEW, True, True),
    ("approver_llm", user(APPROVER), LLM, True, True),
    ("approver_verified", user(APPROVER), VERIFIED, True, True),
    ("admin_verified", user(ADMIN), VERIFIED, True, True),
    ("contrib_verified_not_owner", user(CONTRIB), VERIFIED, False, False),
    ("viewer_verified", user(VIEWER), VERIFIED, False, False),
    # Agents
    ("agent_approver_new", user(APPROVER, AGENT), NEW, True, False),
    ("agent_approver_draft", user(APPROVER, AGENT), DRAFT, True, False),
    ("agent_approver_llm", user(APPROVER, AGENT), LLM, True, False),
    ("agent_approver_verified", user(APPROVER, AGENT), VERIFIED, False, False),
    # ADR 0027: an admin agent may approve changes to verified work and verify.
    ("agent_admin_verified", user(ADMIN, AGENT), VERIFIED, True, True),
    ("agent_admin_new", user(ADMIN, AGENT), NEW, True, True),
    ("agent_contrib_new", user(CONTRIB, AGENT), NEW, False, False),
    # Owners
    ("owner_contrib_llm", user(CONTRIB, name="owner"), LLM, True, False),
    ("owner_contrib_verified", user(CONTRIB, name="owner"), VERIFIED, True, False),
    ("owner_contrib_draft", user(CONTRIB, name="owner"), DRAFT, True, False),
    ("owner_viewer_llm", user(VIEWER, name="owner"), LLM, False, False),
    ("owner_agent_contrib_llm", user(CONTRIB, AGENT, name="owner"), LLM, True, False),
    ("owner_agent_contrib_verified", user(CONTRIB, AGENT, name="owner"), VERIFIED, False, False),
    ("other_contrib_llm", user(CONTRIB, name="not-owner"), LLM, False, False),
    ("contrib_unowned_verified", user(CONTRIB, name="owner"), UNOWNED_VERIFIED, False, False),
    ("approver_unowned_verified", user(APPROVER), UNOWNED_VERIFIED, True, True),
]


@pytest.mark.parametrize(
    ("u", "target", "approve", "verify"),
    [(u, t, a, v) for _, u, t, a, v in APPROVAL_CASES],
    ids=[i for i, *_ in APPROVAL_CASES],
)
def test_can_approve_and_verify(u, target, approve, verify):
    assert can_approve(u, target) is approve
    assert can_verify(u, target) is verify


def test_owner_cannot_self_verify_after_auto_apply():
    # Regression: `owner` comes from the proposer's YAML. A contributor who names themselves
    # owner and gets the definition auto-applied in open mode must still not verify it.
    alice = user(CONTRIB, name="alice")
    own = definition(DefinitionState.LLM_ANNOTATED, owner="alice")
    assert can_auto_apply(TeamMode.OPEN, None, [])
    assert can_approve(alice, own)
    assert not allowed(alice, Tool.VERIFY)
    assert can_verify(alice, own) is False


def test_owner_with_approver_role_can_verify():
    target = definition(DefinitionState.LLM_ANNOTATED, owner="bo")
    assert can_verify(user(APPROVER, name="bo"), target) is True
    assert can_verify(user(APPROVER, AGENT, name="bo"), target) is False


def test_owner_name_must_match_exactly():
    target = definition(DefinitionState.LLM_ANNOTATED, owner="jq")
    assert can_approve(user(CONTRIB, name="jq"), target)
    assert not can_approve(user(CONTRIB, name="JQ"), target)


# ── Four-eyes rule (ADR 0018 addendum) ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "target", [NEW, DRAFT, LLM, VERIFIED], ids=["new", "draft", "llm", "verified"]
)
def test_approver_may_not_approve_own_proposal(target):
    ann = user(APPROVER, name="ann")
    assert can_approve(ann, target, author="ann") is False
    assert can_approve(ann, target, author="bo") is True
    assert can_approve(ann, target) is True  # no author given: rule not applied


def test_owner_may_not_approve_own_proposal():
    owner = user(CONTRIB, name="owner")
    assert can_approve(owner, LLM, author="owner") is False
    assert can_approve(owner, LLM, author="someone-else") is True


def test_admin_is_exempt_from_four_eyes():
    admin = user(ADMIN, name="root")
    assert can_approve(admin, VERIFIED, author="root") is True
    assert can_verify(admin, VERIFIED, author="root") is True


def test_agent_admin_never_verifies_or_approves_own_work():
    """ADR 0027: four-eyes holds for agent admins with no exemption."""
    bot = user(ADMIN, AGENT, name="bot")
    assert can_verify(bot, LLM, author="bot") is False
    assert can_approve(bot, VERIFIED, author="bot") is False
    assert can_approve(bot, LLM, author="bot") is False  # exemption is for humans only
    assert can_approve(bot, None, author="bot") is False
    assert can_approve(bot, LLM, author="someone-else") is True


def test_agent_admin_verifies_and_approves_verified_work_of_others():
    """ADR 0027 reverses "agents never verify" for the admin role only."""
    bot = user(ADMIN, AGENT, name="bot")
    assert can_verify(bot, LLM, author="other") is True
    assert can_approve(bot, VERIFIED, author="other") is True
    for role in (VIEWER, CONTRIB, APPROVER):
        assert can_verify(user(role, AGENT), LLM, author="other") is False
    assert can_approve(user(APPROVER, AGENT), VERIFIED, author="other") is False


def test_approver_may_not_verify_own_version():
    ann = user(APPROVER, name="ann")
    assert can_verify(ann, LLM, author="ann") is False
    assert can_verify(ann, LLM, author="bo") is True
    assert can_verify(ann, LLM) is True


def test_author_match_is_exact():
    ann = user(APPROVER, name="ann")
    assert can_approve(ann, LLM, author="Ann") is True
    assert can_verify(ann, LLM, author="Ann") is True


def test_custom_role_review_all_and_ownership():
    tools = frozenset({Tool.REVIEW_PROPOSAL, Tool.SEARCH})
    owner_only = User(name="owner", role="triage", tools=tools, review_all=False)
    everyone = User(name="tri", role="triage", tools=tools, review_all=True)
    assert can_approve(owner_only, LLM) and not can_approve(owner_only, None)
    assert not can_approve(User(name="x", role="triage", tools=tools, review_all=False), LLM)
    assert can_approve(everyone, None) and can_approve(everyone, VERIFIED)
    no_tool = User(name="owner", role="triage", tools=frozenset({Tool.SEARCH}))
    assert not can_approve(no_tool, LLM)  # owning is not enough without the tool


def test_custom_role_with_verify_lets_a_human_verify_not_an_agent():
    tools = frozenset({Tool.VERIFY})
    assert can_verify(User(name="h", role="qa", tools=tools), LLM, author="x")
    assert not can_verify(User(name="a", role="qa", kind=AGENT, tools=tools), LLM, author="x")


# ── Effective tools (ADR 0027 addendum) ──────────────────────────────────────────────

SUPPORT = Role(
    name="support_bot",
    allowed=frozenset({Tool.SEARCH, Tool.GET_DEFINITION, Tool.COMPILE_AND_RUN, Tool.VERIFY}),
    defaults=frozenset({Tool.SEARCH, Tool.GET_DEFINITION}),
)


def token(mode=ToolMode.FOLLOW_ROLE, include=(), exclude=()) -> TokenInfo:
    import datetime as dt

    return TokenInfo(
        id="t",
        user_name="u",
        name="n",
        prefix="cfg_pat_abcd",
        created_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        created_by="u",
        tool_mode=mode,
        include=frozenset(include),
        exclude=frozenset(exclude),
    )


def test_follow_role_is_defaults_plus_include_minus_exclude_within_allowed():
    tk = token(include={Tool.COMPILE_AND_RUN, Tool.PROPOSE_DEFINITION}, exclude={Tool.SEARCH})
    assert effective_tools(SUPPORT, tk, HUMAN) == {Tool.GET_DEFINITION, Tool.COMPILE_AND_RUN}


def test_follow_role_picks_up_new_defaults():
    tk = token()
    wider = SUPPORT.model_copy(update={"defaults": SUPPORT.allowed})
    assert effective_tools(SUPPORT, tk, HUMAN) == {Tool.SEARCH, Tool.GET_DEFINITION}
    assert Tool.COMPILE_AND_RUN in effective_tools(wider, tk, HUMAN)


def test_only_mode_is_include_within_allowed_and_ignores_defaults():
    tk = token(ToolMode.ONLY, include={Tool.COMPILE_AND_RUN, Tool.MANAGE_USER})
    assert effective_tools(SUPPORT, tk, HUMAN) == {Tool.COMPILE_AND_RUN}
    wider = SUPPORT.model_copy(update={"defaults": SUPPORT.allowed})
    assert effective_tools(wider, tk, HUMAN) == {Tool.COMPILE_AND_RUN}


def test_verify_is_dropped_for_agents_outside_the_admin_role():
    tk = token(include={Tool.VERIFY})
    assert Tool.VERIFY in effective_tools(SUPPORT, tk, HUMAN)
    assert Tool.VERIFY not in effective_tools(SUPPORT, tk, AGENT)
    admin_role = BUILTIN_ROLES[ADMIN]
    assert Tool.VERIFY in effective_tools(admin_role, token(), AGENT)


def test_admin_only_tools_never_leak_into_other_roles_even_if_stored():
    stored = Role.model_construct(
        name="sneaky",
        allowed=frozenset({Tool.SEARCH, Tool.MANAGE_USER}),
        defaults=frozenset({Tool.SEARCH, Tool.MANAGE_USER}),
        review_all=False,
        builtin=False,
        description="",
    )
    assert role_ceiling(stored) == {Tool.SEARCH}
    assert effective_tools(stored, token(), HUMAN) == {Tool.SEARCH}
    assert check_role(stored) != []


def test_admin_role_ceiling_is_every_tool_whatever_is_stored():
    stored = BUILTIN_ROLES[ADMIN].model_copy(
        update={"allowed": frozenset(), "defaults": frozenset()}
    )
    assert role_ceiling(stored) == ALL_TOOLS
    assert check_role(stored) != []


def test_role_tools_allowed_or_defaults():
    assert role_tools(SUPPORT, HUMAN) == SUPPORT.allowed
    assert role_tools(SUPPORT, HUMAN, defaults_only=True) == SUPPORT.defaults
    assert Tool.VERIFY not in role_tools(SUPPORT, AGENT)


def test_check_role_rules():
    assert check_role(SUPPORT) == []
    for tool in ADMIN_ONLY_TOOLS:
        bad = Role.model_construct(
            name="x",
            allowed=frozenset({tool}),
            defaults=frozenset(),
            review_all=False,
            builtin=False,
            description="",
        )
        assert check_role(bad)
    with pytest.raises(ValueError, match="defaults"):
        Role(name="x", allowed=frozenset({Tool.SEARCH}), defaults=frozenset({Tool.VERIFY}))


# ── Admin agents (ADR 0027) ──────────────────────────────────────────────────────────

HUMAN_ADMIN = user(ADMIN, name="root")
AGENT_ADMIN = user(ADMIN, AGENT, name="bot")
OTHER_ADMIN = user(ADMIN, name="boss")
OTHER_AGENT_ADMIN = user(ADMIN, AGENT, name="bot2")
PLAIN = user(CONTRIB, name="carl")
PLAIN_AGENT = user(CONTRIB, AGENT, name="helper")

# (id, actor, target, manage, revoke)
USER_CASES = [
    ("human_admin_on_admin", HUMAN_ADMIN, OTHER_ADMIN, True, True),
    ("human_admin_on_agent_admin", HUMAN_ADMIN, AGENT_ADMIN, True, True),
    ("human_admin_on_plain", HUMAN_ADMIN, PLAIN, True, True),
    ("agent_admin_on_plain", AGENT_ADMIN, PLAIN, True, True),
    ("agent_admin_on_agent", AGENT_ADMIN, PLAIN_AGENT, True, True),
    ("agent_admin_on_human_admin", AGENT_ADMIN, HUMAN_ADMIN, False, False),
    ("agent_admin_on_other_agent_admin", AGENT_ADMIN, OTHER_AGENT_ADMIN, False, False),
    ("agent_admin_on_itself", AGENT_ADMIN, AGENT_ADMIN, False, True),  # own token: yes
    ("approver_on_plain", user(APPROVER, name="ann"), PLAIN, False, False),
    ("plain_on_itself", PLAIN, PLAIN, False, True),
]


@pytest.mark.parametrize(
    ("actor", "target", "manage", "revoke"),
    [(a, t, m, r) for _, a, t, m, r in USER_CASES],
    ids=[i for i, *_ in USER_CASES],
)
def test_who_may_manage_users_and_revoke_tokens(actor, target, manage, revoke):
    assert can_manage_user(actor, target) is manage
    assert can_revoke_token(actor, target) is revoke
    assert can_set_token_tools(actor, target) is revoke


@pytest.mark.parametrize("role", ["viewer", "contributor", "approver", "support_bot"])
def test_agent_admin_may_grant_non_admin_roles_only(role):
    assert can_grant_role(AGENT_ADMIN, role)
    assert can_grant_role(HUMAN_ADMIN, role)
    assert not can_grant_role(PLAIN, role)


def test_only_humans_grant_or_edit_the_admin_role():
    assert can_grant_role(HUMAN_ADMIN, "admin") and not can_grant_role(AGENT_ADMIN, "admin")
    assert can_edit_role(HUMAN_ADMIN, "admin") and not can_edit_role(AGENT_ADMIN, "admin")
    assert can_edit_role(AGENT_ADMIN, "support_bot") and can_edit_role(AGENT_ADMIN, "viewer")
    assert not can_edit_role(user(APPROVER), "support_bot")


def test_tokens_are_minted_only_by_humans():
    assert can_mint_token(PLAIN, PLAIN)  # own
    assert not can_mint_token(PLAIN, user(VIEWER, name="vic"))
    assert can_mint_token(HUMAN_ADMIN, PLAIN_AGENT)
    assert can_mint_token(HUMAN_ADMIN, OTHER_ADMIN)
    for target in (AGENT_ADMIN, PLAIN, PLAIN_AGENT, HUMAN_ADMIN):
        assert not can_mint_token(AGENT_ADMIN, target)
    assert not can_mint_token(PLAIN_AGENT, PLAIN_AGENT)  # an agent, not even its own


def warn(code: IssueCode) -> Issue:
    return Issue(code=code, severity=Severity.WARNING, message=code.value)


def err(code: IssueCode) -> Issue:
    return Issue(code=code, severity=Severity.ERROR, message=code.value)


OPEN, STRICT = TeamMode.OPEN, TeamMode.STRICT

# (id, mode, target, checks, expected)
AUTO_APPLY_CASES = [
    ("open_new_clean", OPEN, NEW, [], True),
    ("open_draft_target", OPEN, DRAFT, [], True),
    ("open_llm_target", OPEN, LLM, [], True),
    ("open_verified_target", OPEN, VERIFIED, [], False),
    ("open_new_warning_ok", OPEN, NEW, [warn(IssueCode.FAN_OUT_RISK)], True),
    (
        "open_new_info_ok",
        OPEN,
        NEW,
        [Issue(code=IssueCode.DRIFT, severity=Severity.INFO, message="x")],
        True,
    ),
    ("open_new_error", OPEN, NEW, [err(IssueCode.COMPILE_FAILED)], False),
    (
        "open_llm_error_among_warnings",
        OPEN,
        LLM,
        [warn(IssueCode.FAN_OUT_RISK), err(IssueCode.TYPE_MISMATCH)],
        False,
    ),
    ("open_new_checks_incomplete_warning", OPEN, NEW, [warn(IssueCode.CHECKS_INCOMPLETE)], False),
    (
        "open_llm_checks_incomplete_info",
        OPEN,
        LLM,
        [Issue(code=IssueCode.CHECKS_INCOMPLETE, severity=Severity.INFO, message="x")],
        False,
    ),
    ("strict_new_clean", STRICT, NEW, [], False),
    ("strict_llm_clean", STRICT, LLM, [], False),
    ("strict_verified_clean", STRICT, VERIFIED, [], False),
]


@pytest.mark.parametrize(
    ("mode", "target", "checks", "expected"),
    [(m, t, c, e) for _, m, t, c, e in AUTO_APPLY_CASES],
    ids=[i for i, *_ in AUTO_APPLY_CASES],
)
def test_can_auto_apply(mode, target, checks, expected):
    assert can_auto_apply(mode, target, checks) is expected


# ── ADR 0026: joins ──────────────────────────────────────────────────────────────────


def test_agent_added_join_never_auto_applies() -> None:
    from cteforge.trust.approval import join_may_auto_apply

    assert not join_may_auto_apply("add", ActorKind.AGENT)
    assert join_may_auto_apply("add", ActorKind.HUMAN)
    assert join_may_auto_apply("annotate", ActorKind.AGENT)


def test_only_a_person_approving_confirms_a_join() -> None:
    from cteforge.trust.approval import confirms_join

    assert confirms_join(ActorKind.HUMAN, auto=False)
    assert not confirms_join(ActorKind.HUMAN, auto=True)  # auto-apply is no approval
    assert not confirms_join(ActorKind.AGENT, auto=False)  # nor an admin agent's
