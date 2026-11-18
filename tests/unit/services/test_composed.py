"""Composed definitions (ADR 0026): proposing, running, trust, floating vs pinned parts,
cycles, nesting, used-by, rename, revalidate, the Library page."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from cteforge.domain.access import Tool
from cteforge.domain.codec import definition_to_yaml
from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import (
    CodecError,
    PermissionDeniedError,
    PolicyError,
    RenderError,
)
from cteforge.domain.issues import IssueCode, Severity
from cteforge.domain.models import (
    Composition,
    Definition,
    DefinitionRef,
    DefinitionState,
    TeamMode,
    TrustLabel,
)
from cteforge.domain.proposals import ProposalKind, ProposalStatus
from cteforge.domain.users import ActorKind, User, UserRole
from cteforge.services import describe, library, propose, review
from cteforge.services.context import AppConfig, AppContext, open_context, set_team_mode
from cteforge.services.parse_query import parse_query
from cteforge.services.revalidate import revalidate
from cteforge.services.run import compile_and_run

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"
META_KEYS = ("id", "title", "meaning", "keywords", "use_when", "dont_use_when", "grain")

ALICE = User(name="alice", role=UserRole.CONTRIBUTOR)
BOT = User(name="bot", role=UserRole.CONTRIBUTOR, kind=ActorKind.AGENT)
ANN = User(name="ann", role=UserRole.APPROVER)
BEN = User(name="ben", role=UserRole.APPROVER)
VIEWER = User(name="vic", role=UserRole.VIEWER)

Q3 = {"start": "2025-07-01", "end": "2025-10-01"}
SELECT = "SELECT rr.region, AVG(rr.realization_rate) AS avg_rate FROM rr GROUP BY rr.region"


@pytest.fixture(scope="module")
def warehouse_copy(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("wh") / "warehouse.duckdb"
    shutil.copy(EXAMPLES / "warehouse.duckdb", path)
    return path


@pytest.fixture
def ctx(warehouse_copy: Path) -> Iterator[AppContext]:
    profile = WarehouseProfile(
        name="demo", dialect=Dialect.DUCKDB, options={"path": str(warehouse_copy)}
    )
    with open_context(AppConfig(db_url="sqlite://"), profiles=[profile]) as c:
        for user in (ALICE, BOT, ANN, BEN, VIEWER):
            c.store.add_user(user)
        yield c


def _example(ctx: AppContext, name: str, user: User = ALICE, **meta: Any):
    sql = (EXAMPLES / "queries" / f"{name}.sql").read_text()
    ann = yaml.safe_load((EXAMPLES / "definitions" / f"{name}.annotations.yml").read_text())
    return propose.propose_definition(
        ctx,
        user,
        sql=sql,
        dialect="duckdb",
        metadata={k: ann[k] for k in META_KEYS} | meta,
        annotations=ann["variables"],
    )


def _compose(ctx: AppContext, user: User = BOT, *, id: str = "rr_by_region", **over: Any):
    composition = {"uses": {"rr": {"id": "realization_rate"}}, "select": SELECT} | over
    return propose.propose_definition(
        ctx,
        user,
        composition=composition,
        dialect="duckdb",
        metadata={"id": id, "title": "Realization by region", "grain": "one row per region"},
    )


@pytest.fixture
def rr(ctx: AppContext) -> AppContext:
    assert _example(ctx, "realization_rate", join_keys=["month", "region"]).auto_applied
    review.verify_definition(ctx, ANN, "realization_rate", 1)
    return ctx


# ── proposing ────────────────────────────────────────────────────────────────────────


def test_propose_composition_checks_and_auto_applies(rr: AppContext) -> None:
    out = _compose(rr)
    assert out.auto_applied and out.issues == []
    stored = rr.store.get_definition("rr_by_region")
    assert stored.state is DefinitionState.LLM_ANNOTATED and stored.composition is not None
    assert stored.composition.uses == {"rr": DefinitionRef(id="realization_rate")}
    assert stored.outputs == ["region", "avg_rate"]
    assert set(stored.tables) == {"deals", "accounts", "invoices"}
    assert stored.variables == []  # parts' variables are not copied: they stay live
    view = review.get_proposal(rr, VIEWER, out.proposal.id)
    assert view.composition is not None and view.composition.compiled_sql
    assert [p.alias for p in view.composition.parts] == ["rr"]
    assert "rr.period" in view.composition.open_variables


def test_exactly_one_of_sql_or_composition(rr: AppContext) -> None:
    with pytest.raises(PolicyError, match="exactly one"):
        propose.propose_definition(
            rr, BOT, sql="SELECT 1", composition={"uses": {}}, metadata={"id": "x", "title": "x"}
        )
    with pytest.raises(PolicyError, match="exactly one"):
        propose.propose_definition(rr, BOT, metadata={"id": "x", "title": "x"})


@pytest.mark.parametrize(
    ("over", "code"),
    [
        ({"uses": {"rr": {"id": "missing"}}}, IssueCode.NOT_FOUND),
        ({"uses": {"rr": {"id": "realization_rate", "version": 9}}}, IssueCode.NOT_FOUND),
        ({"select": "SELECT x.month FROM rr"}, IssueCode.UNKNOWN_ALIAS),
        ({"fixed": {"rr": {"nope": 1}}}, IssueCode.COMPOSITION_INVALID),
        ({"fixed": {"rr": {"regions": "EMEA"}}}, IssueCode.COMPOSITION_INVALID),
        ({"select": "SELECT rr.month FROM rr WHERE no_such_col = 1"}, IssueCode.COMPILE_FAILED),
    ],
)
def test_invalid_compositions_wait_and_cannot_be_approved(
    rr: AppContext, over: dict[str, Any], code: IssueCode
) -> None:
    out = _compose(rr, **over)
    assert code in {i.code for i in out.issues if i.severity is Severity.ERROR}
    assert not out.auto_applied and out.proposal.status is ProposalStatus.PENDING
    assert rr.store.get_definition("rr_by_region") is None
    with pytest.raises(PolicyError, match="failing checks"):
        review.approve(rr, ANN, out.proposal.id)


@pytest.mark.parametrize("select", ["DELETE FROM rr", "SELECT nextval('s') FROM rr"])
def test_not_read_only_select_is_refused(rr: AppContext, select: str) -> None:
    with pytest.raises(PolicyError, match="not_read_only"):
        _compose(rr, select=select)
    assert rr.store.list_proposals(target_id="rr_by_region") == []


def test_malformed_composition_raises(rr: AppContext) -> None:
    with pytest.raises(CodecError):
        _compose(rr, uses={"Bad-Alias": {"id": "realization_rate"}})
    with pytest.raises(CodecError):
        _compose(rr, surprise=1)
    with pytest.raises(CodecError, match="exactly one part"):
        _compose(rr, uses={"a": {"id": "realization_rate"}, "b": {"id": "x"}}, select=None)


def test_annotated_final_select_is_checked_by_comparing_results(rr: AppContext) -> None:
    out = _compose(rr, select="SELECT rr.region, rr.month FROM rr WHERE rr.realization_rate > 0.5")
    assert out.auto_applied
    sql_report = parse_query(
        rr, ALICE, "SELECT rr.region, rr.month FROM rr WHERE rr.realization_rate > 0.5", "duckdb"
    )
    literal = sql_report.literals[0]["id"]
    revised = propose.revise_definition(
        rr,
        BOT,
        id="rr_by_region",
        base_version=1,
        annotations=[
            {
                "name": "min_rate",
                "type": "number",
                "role": "changeable",
                "literals": [literal],
                "description": "Lowest realization rate shown.",
            }
        ],
    )
    assert revised.auto_applied, revised.issues
    stored = rr.store.get_definition("rr_by_region")
    assert [v.name for v in stored.variables] == ["min_rate"] and stored.version == 2
    codes = {i.code: i.severity for i in revised.issues}
    assert codes.get(IssueCode.TYPE_UNKNOWN) is Severity.INFO
    run = compile_and_run(
        rr, BOT, uses={"r": {"id": "rr_by_region"}}, values={"r": {"min_rate": 2}}
    )
    assert run.ran and run.rows == []  # nobody collects twice what they billed


def test_cycles_are_refused_also_through_nesting(rr: AppContext) -> None:
    assert _compose(rr, id="a").auto_applied
    assert _compose(rr, id="b", uses={"x": {"id": "a"}}, select="SELECT * FROM x").auto_applied
    out = propose.revise_definition(
        rr,
        BOT,
        id="a",
        base_version=1,
        composition={"uses": {"y": {"id": "b"}}, "select": "SELECT * FROM y"},
    )
    assert IssueCode.COMPOSITION_CYCLE in {i.code for i in out.issues}
    assert not out.auto_applied
    # A cycle stored behind the checks' back is refused at run time, not looped on.
    a1 = rr.store.get_definition("a")
    rr.store.add_definition_version(
        a1.model_copy(
            update={
                "version": 2,
                "template": "SELECT * FROM y",
                "composition": Composition(uses={"y": DefinitionRef(id="b")}),
            }
        ),
        author="mallory",
    )
    with pytest.raises(PolicyError, match="composition_cycle"):
        compile_and_run(rr, BOT, uses={"q": {"id": "b"}})


# ── running and trust ────────────────────────────────────────────────────────────────


def test_run_by_id_with_dotted_or_per_part_values(rr: AppContext) -> None:
    _compose(rr)
    dotted = compile_and_run(
        rr, BOT, uses={"r": {"id": "rr_by_region"}}, values={"r": {"rr.period": Q3}}
    )
    per_part = compile_and_run(
        rr, BOT, uses={"r": {"id": "rr_by_region"}}, values={"r": {"rr": {"period": Q3}}}
    )
    assert dotted.ran and sorted(dotted.rows) == sorted(per_part.rows)
    assert dotted.columns == ["region", "avg_rate"]
    assert dotted.values_used["r"]["rr.period"] == Q3
    assert dotted.definitions == ["rr_by_region@1", "realization_rate@1"]
    # Verified part, composed definition nobody verified: built_from_verified_parts.
    assert dotted.label is TrustLabel.BUILT_FROM_VERIFIED
    with pytest.raises(RenderError, match="names no part"):
        compile_and_run(rr, BOT, uses={"r": {"id": "rr_by_region"}}, values={"r": {"x.y": 1}})
    with pytest.raises(RenderError, match="no variables of its own|Unknown variable"):
        compile_and_run(rr, BOT, uses={"r": {"id": "rr_by_region"}}, values={"r": {"zz": 1}})


def test_person_verifies_the_whole_then_it_is_verified(rr: AppContext) -> None:
    out = _compose(rr)
    review.verify_definition(rr, ANN, "rr_by_region", 1)
    run = compile_and_run(rr, BOT, uses={"r": {"id": "rr_by_region"}})
    assert run.label is TrustLabel.VERIFIED, run.reasons
    set_team_mode(rr, TeamMode.STRICT)
    assert compile_and_run(rr, BOT, uses={"r": {"id": "rr_by_region"}}).ran
    assert out.proposal.author == "bot"


def test_fixed_values_and_override(rr: AppContext) -> None:
    _compose(rr, fixed={"rr": {"regions": ["APAC"]}})
    card = describe.get_definition(rr, VIEWER, "rr_by_region")
    names = [v.name for v in card.variables]
    assert "rr.regions" not in names and "rr.period" in names
    assert card.composition.fixed == {"rr": {"regions": ["APAC"]}}
    run = compile_and_run(rr, BOT, uses={"r": {"id": "rr_by_region"}})
    assert {row[0] for row in run.rows} <= {"APAC"}
    override = compile_and_run(
        rr, BOT, uses={"r": {"id": "rr_by_region"}}, values={"r": {"rr.regions": ["EMEA"]}}
    )
    assert IssueCode.FIXED_OVERRIDE in {i.code for i in override.issues}
    assert override.label is TrustLabel.LLM_ANNOTATED  # one step below built_from_verified


def test_floating_part_follows_fixes_and_becomes_unusable(rr: AppContext) -> None:
    _compose(rr)
    _compose(rr, id="pinned", uses={"rr": {"id": "realization_rate", "version": 1}})
    propose.revise_definition(
        rr, ALICE, id="realization_rate", base_version=1, metadata={"title": "RR v2"}
    )
    review.approve(rr, ANN, rr.store.list_proposals(ProposalStatus.PENDING)[0].id)
    floating = compile_and_run(rr, BOT, uses={"r": {"id": "rr_by_region"}})
    assert "realization_rate@2" in floating.definitions
    pinned = compile_and_run(rr, BOT, uses={"p": {"id": "pinned"}})
    assert "realization_rate@1" in pinned.definitions
    assert IssueCode.PART_SUPERSEDED in {i.code for i in pinned.issues}
    card = describe.get_definition(rr, VIEWER, "pinned")
    assert card.composition.parts[0].superseded and card.composition.parts[0].version == 1
    # Strict mode with no verified version of the floating part left: unusable.
    set_team_mode(rr, TeamMode.STRICT)
    rr.store.set_definition_state("realization_rate", 1, DefinitionState.DRAFT)
    blocked = compile_and_run(rr, BOT, uses={"r": {"id": "rr_by_region"}})
    assert not blocked.usable and not blocked.ran
    part_issue = [i for i in blocked.issues if i.code is IssueCode.PART_NOT_USABLE]
    assert part_issue and "'rr'" in part_issue[0].message
    assert "realization_rate" in part_issue[0].message


def test_nested_composition_runs_and_labels_inner_states(rr: AppContext) -> None:
    _compose(rr)
    out = _compose(
        rr,
        id="top_regions",
        uses={"by_region": {"id": "rr_by_region"}},
        select="SELECT region FROM by_region ORDER BY avg_rate DESC LIMIT 1",
    )
    assert out.auto_applied, out.issues
    run = compile_and_run(
        rr,
        BOT,
        uses={"t": {"id": "top_regions"}},
        values={"t": {"by_region.rr.period": Q3}},
    )
    assert run.ran and run.row_count == 1
    assert run.definitions == ["top_regions@1", "rr_by_region@1", "realization_rate@1"]
    assert run.values_used["t"]["by_region.rr.period"] == Q3
    card = describe.get_definition(rr, VIEWER, "top_regions")
    assert "by_region.rr.period" in [v.name for v in card.variables]
    assert card.composition.parts[0].composed


# ── used by, rename, revalidate ──────────────────────────────────────────────────────


def test_used_by_in_card_references_and_impact(rr: AppContext) -> None:
    _compose(rr)
    card = describe.get_definition(rr, VIEWER, "realization_rate")
    assert [(u.definition_id, u.alias, u.pinned_version) for u in card.used_by] == [
        ("rr_by_region", "rr", None)
    ]
    refs = describe.definition_references(rr, VIEWER, "realization_rate")
    assert [u.definition_id for u in refs.used_by] == ["rr_by_region"]
    set_team_mode(rr, TeamMode.STRICT)
    pending = _compose(rr, id="also_uses")
    refs = describe.definition_references(rr, VIEWER, "realization_rate")
    assert [(p.proposal_id, p.relation) for p in refs.proposals] == [(pending.proposal.id, "uses")]
    change = propose.revise_definition(
        rr, ALICE, id="realization_rate", base_version=1, metadata={"title": "x"}
    )
    assert review.get_proposal(rr, VIEWER, change.proposal.id).impact == ["rr_by_region"]
    # The card's joins-to list: edges touching its tables (mined from it).
    assert {j.id for j in card.joins} >= {"accounts__deals__account_id=account_id"}


def test_rename_updates_uses_and_runs(rr: AppContext) -> None:
    _compose(rr)
    _compose(rr, id="pinned", uses={"rr": {"id": "realization_rate", "version": 1}})
    set_team_mode(rr, TeamMode.STRICT)
    pending = _compose(rr, id="later")
    out = propose.rename_definition(rr, ALICE, id="realization_rate", new_id="realization")
    assert out.proposal.payload["references"]["used_by"]
    review.approve(rr, ANN, out.proposal.id)
    stored = rr.store.get_definition("rr_by_region")
    assert stored.composition.uses["rr"].id == "realization"
    assert rr.store.get_definition("pinned").composition.uses["rr"] == DefinitionRef(
        id="realization", version=1
    )
    proposed = rr.store.get_proposal(pending.proposal.id).payload["definition_yaml"]
    assert "id: realization\n" in proposed
    run = compile_and_run(rr, BOT, uses={"r": {"id": "rr_by_region"}})
    assert run.ran and "realization@1" in run.definitions
    review.approve(rr, ANN, pending.proposal.id)
    assert rr.store.get_definition("later").composition.uses["rr"].id == "realization"


def test_revalidate_composed_definition(rr: AppContext) -> None:
    _compose(rr)
    (report,) = revalidate(rr, BOT, ["rr_by_region"])
    assert report.checked and report.ok, report.issues
    # A part made unusable: the composed definition drifts.
    bad = rr.store.get_definition("realization_rate").model_copy(
        update={"version": 2, "template": "SELECT 1 AS other_column", "variables": []}
    )
    rr.store.add_definition_version(bad, author="mallory")
    (report,) = revalidate(rr, BOT, ["rr_by_region"])
    assert not report.ok and report.issues[0].code is IssueCode.DRIFT


def test_mining_includes_composed_definitions(rr: AppContext) -> None:
    _compose(rr)
    edge = rr.store.get_join("accounts__deals__account_id=account_id")
    assert edge.sources == ["realization_rate", "rr_by_region"]


# ── the Library page ─────────────────────────────────────────────────────────────────


def test_library_rows_filters_and_needs_my_review(rr: AppContext) -> None:
    _compose(rr)  # by an agent, auto-applied
    set_team_mode(rr, TeamMode.STRICT)
    waiting = _compose(rr, id="waiting")
    revision = propose.revise_definition(
        rr, BOT, id="realization_rate", base_version=1, metadata={"title": "x"}
    )
    rejected = _compose(rr, id="nope")
    review.reject(rr, ANN, rejected.proposal.id, "no")
    page = library.list_library(rr, ANN)
    rows = {r.id: r for r in page.rows}
    assert set(rows) == {"realization_rate", "rr_by_region", "waiting"}  # rejected hidden
    assert page.pending_count == 2 and page.proposals_visible
    assert rows["waiting"].version is None and rows["waiting"].approval == "pending"
    assert rows["waiting"].latest_proposal.id == waiting.proposal.id
    assert rows["rr_by_region"].composed and rows["rr_by_region"].created_by_kind == "agent"
    rr_row = rows["realization_rate"]
    assert [p.id for p in rr_row.pending] == [revision.proposal.id]
    assert rr_row.verified_by == "ann" and rr_row.verified_by_kind == "human"
    assert rr_row.approval == "pending"
    every = library.list_library(rr, ANN, approval=["rejected"])
    assert [r.id for r in every.rows] == ["nope"]
    assert [r.id for r in library.list_library(rr, ANN, trust=["verified"]).rows] == [
        "realization_rate"
    ]
    agents = library.list_library(rr, ANN, created_by=["agent"])
    assert {r.id for r in agents.rows} == {"rr_by_region", "waiting"}
    assert [r.id for r in library.list_library(rr, ANN, verified_by=["human"]).rows] == [
        "realization_rate"
    ]
    assert library.list_library(rr, ANN, verified_by=["agent"]).rows == []
    mine = library.list_library(rr, ANN, needs_my_review=True)
    assert {r.id for r in mine.rows} == {"realization_rate", "waiting"}
    assert [r.id for r in library.list_library(rr, ANN, q="rr_by_region").rows] == ["rr_by_region"]
    # Four-eyes: the author does not need to review their own proposal.
    with pytest.raises(PermissionDeniedError):
        library.list_library(rr, VIEWER, needs_my_review=True)
    # Without list_proposals: stored definitions only, no proposal data.
    narrow = VIEWER.model_copy(update={"tools": frozenset({Tool.SEARCH})})
    plain = library.list_library(rr, narrow)
    assert not plain.proposals_visible and plain.pending_count == 0
    assert {r.id for r in plain.rows} == {"realization_rate", "rr_by_region"}
    assert all(r.pending == [] and r.latest_proposal is None for r in plain.rows)


def test_definition_yaml_of_a_composed_proposal_round_trips(rr: AppContext) -> None:
    out = _compose(rr)
    text = out.proposal.payload["definition_yaml"]
    assert "composition:" in text and "schema_version: 2" in text
    stored = rr.store.get_definition("rr_by_region")
    assert definition_to_yaml(stored).count("composition:") == 1
    assert isinstance(stored, Definition) and stored.composed
    assert out.proposal.kind is ProposalKind.NEW_DEFINITION


def test_raw_table_reads_in_a_composed_definition_need_check_and_run(rr: AppContext) -> None:
    """ADR 0027 security review, kept for composed definitions (ADR 0026): a stored final
    SELECT over raw tables is exploratory SQL whoever runs it — verified or not, nested
    or not — and so are the proposal checks that run it."""
    raw = "SELECT a.name, (SELECT COUNT(*) FROM rr) AS n FROM accounts AS a"
    out = _compose(rr, ALICE, id="raw_combo", select=raw)
    assert out.auto_applied, out.issues
    review.verify_definition(rr, ANN, "raw_combo", 1)
    _compose(rr, ALICE, id="outer", uses={"x": {"id": "raw_combo"}}, select="SELECT name FROM x")
    narrow = BOT.model_copy(update={"tools": frozenset({Tool.SEARCH, Tool.COMPILE_AND_RUN})})
    for id in ("raw_combo", "outer"):
        with pytest.raises(PermissionDeniedError, match="check_and_run"):
            compile_and_run(rr, narrow, uses={"c": {"id": id}})
        assert compile_and_run(rr, BOT, uses={"c": {"id": id}}).ran  # BOT holds it
    builder = BOT.model_copy(update={"tools": frozenset({Tool.PROPOSE_DEFINITION})})
    with pytest.raises(PermissionDeniedError, match="check_and_run"):
        _compose(rr, builder, id="raw_again", select=raw)
    assert rr.store.get_definition("raw_again") is None
    # Over the aliases only, nothing extra is needed.
    assert _compose(rr, builder, id="clean").auto_applied
