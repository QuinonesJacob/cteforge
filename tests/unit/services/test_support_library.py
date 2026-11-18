"""Shared support code: services/_library.py, the audit outcome rule in _audit.py, and
cards.json_safe on enums. SQLite store only; no warehouse."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from cteforge.domain.audit import AuditKind, AuditOutcome
from cteforge.domain.codec import definition_to_yaml
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


def test_full_proposal_diff_keeps_every_unchanged_line() -> None:
    """ADR 0029: the GUI's Full view asks for the whole file as context."""
    base = example()
    changed = base.model_copy(update={"version": 2, "title": "A new title"})
    p = Proposal(
        id="p2",
        kind=ProposalKind.REVISE_DEFINITION,
        target_id=base.id,
        base_version=1,
        payload={"definition_yaml": definition_to_yaml(changed)},
        author="a",
        author_kind=ActorKind.HUMAN,
        created_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
    )
    short, full = proposal_diff(p, base), proposal_diff(p, base, full=True)
    body = [line for line in full.splitlines() if not line.startswith(("---", "+++"))]
    assert sum(line.startswith("@@") for line in body) == 1  # one hunk: the whole file
    context = [line[1:] for line in body if line.startswith(" ")]
    removed = [line[1:] for line in body if line.startswith("-")]
    assert context + removed  # every line of the base appears, as context or removed
    assert len(context) + len(removed) == len(definition_to_yaml(base).splitlines())
    assert len(full.splitlines()) > len(short.splitlines())
    changed_lines = [
        ln for ln in full.splitlines() if ln[:1] in "+-" and ln[:3] not in ("+++", "---")
    ]
    assert changed_lines == [
        ln for ln in short.splitlines() if ln[:1] in "+-" and ln[:3] not in ("+++", "---")
    ]


# ── join map: where each source makes a join (ADR 0029) ──────────────────────────────

PLACES_SQL = """
WITH won AS (
  SELECT d.deal_id, a.region FROM deals AS d JOIN accounts AS a ON a.account_id = d.account_id
),
nested AS (
  SELECT x.deal_id FROM (
    SELECT d.deal_id FROM deals AS d LEFT JOIN invoices AS i ON i.deal_id = d.deal_id
  ) AS x
)
SELECT w.region, i.paid_amount
FROM won AS w
LEFT JOIN invoices AS i ON i.deal_id = w.deal_id
WHERE w.deal_id IN (
  SELECT d2.deal_id FROM deals AS d2 JOIN accounts AS a2 ON a2.account_id = d2.account_id
)
"""


def _places(edge) -> list[tuple[str, str, str, str | None]]:
    return [(s.definition, s.join_type, s.location, s.scope) for s in edge.source_joins]


def test_mined_edges_record_join_type_and_location_per_source(ctx: AppContext) -> None:
    definition = Definition(
        id="places", title="places", source_dialect=Dialect.DUCKDB, template=PLACES_SQL
    )
    edges = {e.id: e for e in _library.mined_edges(definition)}
    accounts = edges["accounts__deals__account_id=account_id"]
    invoices = edges["deals__invoices__deal_id=deal_id"]
    assert sorted(_places(accounts)) == [
        ("places", "inner", "cte", "won"),
        ("places", "inner", "subquery", None),  # IN (subquery) in the outer query
    ]
    assert sorted(_places(invoices)) == [
        ("places", "left", "main", None),  # through the CTE `won` to deals
        ("places", "left", "subquery", "nested"),  # a derived table inside a CTE
    ]
    # Stored by refresh_join_map, merged across definitions and sorted.
    ctx.store.add_definition_version(definition, author="analyst")
    ctx.store.add_definition_version(example(), author="analyst")
    _library.refresh_join_map(ctx)
    stored = ctx.store.get_join("accounts__deals__account_id=account_id")
    assert stored.sources == ["places", "realization_rate"]
    assert _places(stored) == [
        ("places", "inner", "cte", "won"),
        ("places", "inner", "subquery", None),
        ("realization_rate", "inner", "cte", "deals_in_region"),
    ]


def test_left_join_type_is_oriented_like_the_edge(ctx: AppContext) -> None:
    """`invoices LEFT JOIN deals` is stored on deals__invoices as a RIGHT join."""
    sql = "SELECT i.deal_id FROM invoices AS i LEFT JOIN deals AS d ON d.deal_id = i.deal_id"
    definition = Definition(id="flip", title="flip", source_dialect=Dialect.DUCKDB, template=sql)
    [edge] = _library.mined_edges(definition)
    assert edge.left_table == "deals" and edge.join_type == "right"
    assert _places(edge) == [("flip", "right", "main", None)]


def test_join_cards_carry_source_states_and_titles(ctx: AppContext) -> None:
    ctx.store.add_definition_version(
        example(state=DefinitionState.VERIFIED, verified_by="ann", verified_at=dt.date.today()),
        author="analyst",
    )
    _library.refresh_join_map(ctx)
    [card, *_] = _library.join_cards(ctx, AGENT, ctx.store.list_joins())
    assert card.source_states == {"realization_rate": DefinitionState.VERIFIED}
    assert card.source_titles == {"realization_rate": example().title}
