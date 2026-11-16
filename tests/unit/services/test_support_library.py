"""Shared support code: services/_library.py, the audit outcome rule in _audit.py, and
cards.json_safe on enums. SQLite store only; no warehouse."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from cteforge.domain.audit import AuditKind, AuditOutcome
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import (
    NotFoundError,
    PermissionDeniedError,
    PolicyError,
    StaleVersionError,
)
from cteforge.domain.models import Definition, DefinitionState, JoinKey, TeamMode
from cteforge.domain.proposals import Proposal, ProposalKind
from cteforge.domain.users import ActorKind, User, UserRole
from cteforge.files.definitions import load_definition
from cteforge.services import _apply, _audit, _library
from cteforge.services.cards import json_safe, proposal_diff
from cteforge.services.context import AppConfig, AppContext, open_context
from cteforge.services.import_query import import_query
from cteforge.services.review import withdraw

ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = ROOT / "examples"
AGENT = User(name="claude-agent", role=UserRole.CONTRIBUTOR, kind=ActorKind.AGENT)


@pytest.fixture
def ctx(tmp_path: Path):
    with open_context(AppConfig(db_url=f"sqlite:///{tmp_path / 'lib.db'}"), profiles=[]) as c:
        yield c


def example(name: str = "realization_rate", **update) -> Definition:
    return load_definition(EXAMPLES / "definitions" / f"{name}.yml").model_copy(update=update)


def add(ctx: AppContext, version: int, state: DefinitionState) -> Definition:
    return ctx.store.add_definition_version(example(version=version, state=state), author="analyst")


# ── inputs and visibility ────────────────────────────────────────────────────────────


def test_parse_dialect_refuses_unknown_names_with_policy_error() -> None:
    assert _library.parse_dialect("snowflake") is Dialect.SNOWFLAKE
    assert _library.parse_dialect(Dialect.DUCKDB) is Dialect.DUCKDB
    with pytest.raises(PolicyError, match="Unknown dialect 'oracle'"):
        _library.parse_dialect("oracle")


def test_usable_and_visible_states_follow_the_team_mode() -> None:
    v, a, d = DefinitionState.VERIFIED, DefinitionState.LLM_ANNOTATED, DefinitionState.DRAFT
    assert _library.usable_states(TeamMode.STRICT) == {v}
    assert _library.usable_states(TeamMode.OPEN) == {v, a}
    assert _library.visible_states(TeamMode.STRICT, include_drafts=False) == {v}
    assert _library.visible_states(TeamMode.STRICT, include_drafts=True) == {v, a, d}


def test_newest_in_states_skips_newer_versions_in_other_states(ctx: AppContext) -> None:
    add(ctx, 1, DefinitionState.VERIFIED)
    add(ctx, 2, DefinitionState.LLM_ANNOTATED)
    latest = add(ctx, 3, DefinitionState.DRAFT)
    strict = _library.usable_states(TeamMode.STRICT)
    open_ = _library.usable_states(TeamMode.OPEN)
    found_strict = _library.newest_in_states(ctx, latest, strict)
    found_open = _library.newest_in_states(ctx, latest, open_)
    assert found_strict is not None and found_strict.version == 1
    assert found_open is not None and found_open.version == 2
    assert _library.newest_in_states(ctx, latest, set(DefinitionState)) == latest
    assert _library.newest_in_states(ctx, latest, set()) is None


# ── rebuilding a stored definition ───────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["realization_rate", "booked_vs_collected"])
def test_rebuilt_query_and_annotations_reproduce_the_definition(name: str) -> None:
    stored = example(name)
    outcome = import_query(
        _library.original_sql(stored), stored.source_dialect, _library.annotation_file(stored)
    )
    rebuilt = outcome.definition
    assert rebuilt.template == stored.template
    assert [v.name for v in rebuilt.variables] == [v.name for v in stored.variables]
    assert [v.slots for v in rebuilt.variables] == [v.slots for v in stored.variables]
    assert outcome.roundtrip.identical
    header = _library.annotation_file(stored)
    assert (header.id, header.title, header.grain) == (stored.id, stored.title, stored.grain)
    assert header.variables == _library.annotations_of(stored)


# ── join map ─────────────────────────────────────────────────────────────────────────


def test_canonical_edge_and_matching_agree_in_both_orientations() -> None:
    edge = _library.canonical_edge(
        "Deals",
        "accounts",
        [JoinKey(left="account_id", right="id")],
        "left",
        relationship="many_to_one",
    )
    assert edge.id == "accounts__deals__id=account_id"
    assert (edge.left_table, edge.join_type, edge.relationship) == (
        "accounts",
        "right",
        "one_to_many",
    )
    assert _library.matching_edge("deals", "accounts", [("account_id", "id")], [edge]) is edge
    assert _library.matching_edge("sales.accounts", "deals", [("ID", "Account_Id")], [edge]) is edge
    assert _library.matching_edge("deals", "accounts", [("id", "account_id")], [edge]) is None


def test_same_table_matches_qualified_by_last_part_only_when_one_side_is_bare() -> None:
    assert _library.same_table("Sales.Deals", "deals")
    assert not _library.same_table("sales.deals", "crm.deals")
    assert not _library.same_table("deals", "accounts")


# ── audit outcome rule ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (PermissionDeniedError("no"), AuditOutcome.REJECTED),
        (PolicyError("no"), AuditOutcome.REJECTED),
        (StaleVersionError("old"), AuditOutcome.REJECTED),
        (NotFoundError("missing"), AuditOutcome.ERROR),
    ],
)
def test_audit_failures_records_the_outcome_rule_and_reraises(
    ctx: AppContext, error: Exception, outcome: AuditOutcome
) -> None:
    assert _audit.failure_outcome(error) is outcome
    with pytest.raises(type(error)):
        with _audit.audit_failures(ctx, AGENT, AuditKind.SEARCH, extra={"op": "x"}):
            raise error
    event = ctx.audit.list_events(limit=1)[0]
    assert event.outcome is outcome and event.error == type(error).__name__
    assert event.extra == {"op": "x"} and event.actor == AGENT.name


def test_audit_failures_records_nothing_on_success(ctx: AppContext) -> None:
    with _audit.audit_failures(ctx, AGENT, AuditKind.SEARCH):
        pass
    assert ctx.audit.list_events(limit=10) == []


# ── permissions added during integration ─────────────────────────────────────────────


def test_withdraw_needs_the_contributor_role_even_for_the_author(ctx: AppContext) -> None:
    edge = _library.canonical_edge("deals", "accounts", [("account_id", "account_id")])
    proposal = _apply.new_proposal(
        ctx, AGENT, ProposalKind.NEW_JOIN, edge.id, {"join": edge.model_dump(mode="json")}, []
    )
    ctx.store.add_proposal(proposal)
    demoted = AGENT.model_copy(update={"role": UserRole.VIEWER})
    with pytest.raises(PermissionDeniedError):
        withdraw(ctx, demoted, proposal.id)
    assert ctx.audit.list_events(limit=1)[0].outcome is AuditOutcome.REJECTED
    assert withdraw(ctx, AGENT, proposal.id).status.value == "withdrawn"


# ── cards ────────────────────────────────────────────────────────────────────────────


def test_json_safe_turns_enums_into_plain_values() -> None:
    value = json_safe({"state": DefinitionState.VERIFIED, "modes": [TeamMode.OPEN]})
    assert value == {"state": "verified", "modes": ["open"]}
    assert type(value["state"]) is str
    json.dumps(value)


def test_proposal_diff_accepts_enum_members_in_a_payload() -> None:
    p = Proposal(
        id="p1",
        kind=ProposalKind.NEW_JOIN,
        target_id="j",
        payload={"other": {"state": DefinitionState.DRAFT}},
        author="a",
        author_kind=ActorKind.AGENT,
        created_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
    )
    assert "+  state: draft" in proposal_diff(p, None).splitlines()
