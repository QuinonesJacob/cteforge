import pytest

from cteforge.domain.dialects import Dialect
from cteforge.domain.issues import Issue, IssueCode, Severity
from cteforge.domain.models import Definition, DefinitionState, TeamMode
from cteforge.domain.users import ActorKind, User, UserRole
from cteforge.trust import Action, allowed, can_approve, can_auto_apply, can_verify

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

# Minimum role per action (ADR 0018), checked against every role for humans.
MINIMUM = {
    Action.SEARCH: VIEWER,
    Action.DESCRIBE: VIEWER,
    Action.RUN: VIEWER,
    Action.PROPOSE: CONTRIB,
    Action.APPROVE: APPROVER,
    Action.VERIFY: APPROVER,
    Action.MANAGE_USERS: ADMIN,
    Action.MANAGE_SETTINGS: ADMIN,
}
ORDER = [VIEWER, CONTRIB, APPROVER, ADMIN]


def test_every_action_has_a_minimum():
    assert set(MINIMUM) == set(Action)


@pytest.mark.parametrize("role", ORDER)
@pytest.mark.parametrize("action", list(Action))
def test_allowed_role_minimums_for_humans(action, role):
    expected = ORDER.index(role) >= ORDER.index(MINIMUM[action])
    assert allowed(user(role), action) is expected


@pytest.mark.parametrize("role", ORDER)
@pytest.mark.parametrize("action", list(Action))
def test_agents_same_as_humans_except_verify(action, role):
    expected = ORDER.index(role) >= ORDER.index(MINIMUM[action])
    if action is Action.VERIFY:
        expected = False
    assert allowed(user(role, AGENT), action) is expected


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
    ("agent_admin_verified", user(ADMIN, AGENT), VERIFIED, False, False),
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
    assert not allowed(alice, Action.VERIFY)
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


def test_agent_admin_still_never_verifies_or_approves_verified_own_work():
    bot = user(ADMIN, AGENT, name="bot")
    assert can_verify(bot, LLM, author="bot") is False
    assert can_approve(bot, VERIFIED, author="bot") is False
    assert can_approve(bot, LLM, author="bot") is True  # admin exemption; not verified


def test_approver_may_not_verify_own_version():
    ann = user(APPROVER, name="ann")
    assert can_verify(ann, LLM, author="ann") is False
    assert can_verify(ann, LLM, author="bo") is True
    assert can_verify(ann, LLM) is True


def test_author_match_is_exact():
    ann = user(APPROVER, name="ann")
    assert can_approve(ann, LLM, author="Ann") is True
    assert can_verify(ann, LLM, author="Ann") is True


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
