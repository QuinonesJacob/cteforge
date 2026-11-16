from __future__ import annotations

import datetime as dt

import pytest

from cteforge.domain.errors import StoreError
from cteforge.domain.issues import Issue, IssueCode, Severity
from cteforge.domain.proposals import Proposal, ProposalKind, ProposalStatus
from cteforge.domain.users import ActorKind, User, UserRole
from cteforge.store import Store

T0 = dt.datetime(2026, 11, 16, 9, 0, tzinfo=dt.UTC)


def proposal(id: str, minutes: int, target: str = "realization_rate", **extra: object) -> Proposal:
    data: dict[str, object] = {
        "id": id,
        "kind": ProposalKind.REVISE_DEFINITION,
        "target_id": target,
        "base_version": 1,
        "payload": {"definition_yaml": "id: realization_rate\nversion: 2\n"},
        "checks": [
            Issue(code=IssueCode.CHECKS_INCOMPLETE, severity=Severity.WARNING, message="no wh")
        ],
        "summary": "tighten region filter",
        "author": "claude-agent",
        "author_kind": ActorKind.AGENT,
        "created_at": T0 + dt.timedelta(minutes=minutes),
    }
    return Proposal.model_validate(data | extra)


# ------------------------------------------------------------------- proposals


def test_proposal_round_trip(store: Store) -> None:
    p = proposal("p1", 0)
    assert store.add_proposal(p) == p
    got = store.get_proposal("p1")
    assert got == p
    assert got.checks[0].code is IssueCode.CHECKS_INCOMPLETE
    assert got.created_at.tzinfo is not None
    assert store.get_proposal("nope") is None


def test_duplicate_proposal_id_raises(store: Store) -> None:
    store.add_proposal(proposal("p1", 0))
    with pytest.raises(StoreError):
        store.add_proposal(proposal("p1", 5))
    assert store.get_proposal("p1").created_at == T0


def test_list_proposals_newest_first(store: Store) -> None:
    store.add_proposal(proposal("middle", 10))
    store.add_proposal(proposal("oldest", 0))
    store.add_proposal(proposal("newest", 20))
    # Non-UTC offset is ordered by the instant, not the wall-clock text.
    tokyo = dt.timezone(dt.timedelta(hours=9))
    store.add_proposal(
        proposal("tokyo", 0, created_at=(T0 + dt.timedelta(minutes=15)).astimezone(tokyo))
    )
    assert [p.id for p in store.list_proposals()] == ["newest", "tokyo", "middle", "oldest"]


def test_list_proposals_filters(store: Store) -> None:
    store.add_proposal(proposal("a", 0))
    store.add_proposal(proposal("b", 1, target="pipeline"))
    store.add_proposal(proposal("c", 2, status=ProposalStatus.REJECTED))
    assert [p.id for p in store.list_proposals(status=ProposalStatus.PENDING)] == ["b", "a"]
    assert [p.id for p in store.list_proposals(target_id="realization_rate")] == ["c", "a"]
    assert [
        p.id
        for p in store.list_proposals(status=ProposalStatus.PENDING, target_id="realization_rate")
    ] == ["a"]
    assert store.list_proposals(status=ProposalStatus.WITHDRAWN) == []


def test_update_proposal_replaces_and_reindexes(store: Store) -> None:
    store.add_proposal(proposal("p1", 0))
    decided = store.get_proposal("p1").model_copy(
        update={
            "status": ProposalStatus.APPROVED,
            "decided_by": "ann",
            "decided_at": T0 + dt.timedelta(hours=1),
            "comment": "looks right",
            "resulting_version": 2,
        }
    )
    assert store.update_proposal(decided) == decided
    assert store.get_proposal("p1") == decided
    assert store.list_proposals(status=ProposalStatus.PENDING) == []
    assert [p.id for p in store.list_proposals(status=ProposalStatus.APPROVED)] == ["p1"]


def test_update_missing_proposal_raises(store: Store) -> None:
    with pytest.raises(StoreError):
        store.update_proposal(proposal("ghost", 0))
    assert store.get_proposal("ghost") is None


# ----------------------------------------------------------------------- users


def test_add_user_sets_created_at_and_round_trips(store: Store) -> None:
    added = store.add_user(User(name="jq", role=UserRole.ADMIN), "a" * 64)
    assert added.created_at is not None
    assert added.created_at.utcoffset() == dt.timedelta(0)
    assert store.get_user("jq") == added
    assert store.get_user("nobody") is None


def test_add_user_keeps_given_created_at(store: Store) -> None:
    user = User(name="bot", role=UserRole.CONTRIBUTOR, kind=ActorKind.AGENT, created_at=T0)
    store.add_user(user, "b" * 64)
    got = store.get_user("bot")
    assert (got.kind, got.role, got.created_at) == (ActorKind.AGENT, UserRole.CONTRIBUTOR, T0)


def test_duplicate_name_or_token_raises(store: Store) -> None:
    store.add_user(User(name="jq", role=UserRole.ADMIN), "a" * 64)
    with pytest.raises(StoreError):
        store.add_user(User(name="jq", role=UserRole.VIEWER), "c" * 64)
    with pytest.raises(StoreError):
        store.add_user(User(name="other", role=UserRole.VIEWER), "a" * 64)
    assert [u.name for u in store.list_users()] == ["jq"]
    assert store.get_user("jq").role is UserRole.ADMIN


def test_user_by_token(store: Store) -> None:
    store.add_user(User(name="jq", role=UserRole.ADMIN), "a" * 64)
    store.add_user(User(name="ann", role=UserRole.APPROVER), "b" * 64)
    assert store.user_by_token("b" * 64).name == "ann"
    assert store.user_by_token("a" * 64).name == "jq"
    assert store.user_by_token("f" * 64) is None


def test_list_users_sorted_by_name(store: Store) -> None:
    for i, name in enumerate(["zed", "ann", "mo"]):
        store.add_user(User(name=name, role=UserRole.VIEWER), str(i) * 64)
    assert [u.name for u in store.list_users()] == ["ann", "mo", "zed"]


def test_set_user_role(store: Store) -> None:
    store.add_user(User(name="ann", role=UserRole.VIEWER), "a" * 64)
    assert store.set_user_role("ann", UserRole.APPROVER).role is UserRole.APPROVER
    assert store.get_user("ann").role is UserRole.APPROVER
    with pytest.raises(StoreError):
        store.set_user_role("nobody", UserRole.ADMIN)


def test_set_user_token_rotates(store: Store) -> None:
    store.add_user(User(name="ann", role=UserRole.VIEWER), "a" * 64)
    store.add_user(User(name="jq", role=UserRole.ADMIN), "b" * 64)
    store.set_user_token("ann", "c" * 64)
    assert store.user_by_token("a" * 64) is None
    assert store.user_by_token("c" * 64).name == "ann"
    with pytest.raises(StoreError):
        store.set_user_token("ann", "b" * 64)  # belongs to jq
    assert store.user_by_token("b" * 64).name == "jq"
    with pytest.raises(StoreError):
        store.set_user_token("nobody", "d" * 64)


# -------------------------------------------------------------------- settings


def test_settings(store: Store) -> None:
    assert store.get_setting("team_mode") is None
    store.set_setting("team_mode", "open")
    assert store.get_setting("team_mode") == "open"
    store.set_setting("team_mode", "strict")
    assert store.get_setting("team_mode") == "strict"
    store.set_setting("other", "")
    assert store.get_setting("other") == ""
    assert store.get_setting("team_mode") == "strict"
