"""Join map (mining + propose_join add/annotate, ADR 0026), library export/import with
composed definitions and legacy recipe files (DESIGN §3, §17, §18; ADR 0003)."""

from __future__ import annotations

import datetime as dt
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import CodecError, PermissionDeniedError, PolicyError
from cteforge.domain.issues import IssueCode, Severity
from cteforge.domain.models import DefinitionState, TeamMode
from cteforge.domain.proposals import ProposalKind, ProposalStatus
from cteforge.domain.users import ActorKind, User, UserRole
from cteforge.services import review
from cteforge.services.context import AppConfig, AppContext, open_context, set_team_mode
from cteforge.services.library_io import export_library, import_library
from cteforge.services.propose import propose_definition, revise_definition
from cteforge.services.propose_other import propose_join

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"
FIXED = dt.datetime(2026, 11, 16, 9, 30, tzinfo=dt.UTC)
META_KEYS = ("id", "title", "meaning", "keywords", "use_when", "dont_use_when", "grain")

ALICE = User(name="alice", role=UserRole.CONTRIBUTOR)
BOT = User(name="bot", role=UserRole.CONTRIBUTOR, kind=ActorKind.AGENT)
APPROVER = User(name="ann", role=UserRole.APPROVER)
AGENT_APPROVER = User(name="robo", role=UserRole.APPROVER, kind=ActorKind.AGENT)
VIEWER = User(name="vic", role=UserRole.VIEWER)

REGION_SQL = """
SELECT a.region, SUM(d.amount) AS booked
FROM deals AS d
JOIN accounts AS a ON a.account_id = d.account_id
WHERE d.status = 'closed_won'
GROUP BY a.region
"""
NO_JOIN_SQL = """
SELECT d.account_id, SUM(d.amount) AS booked
FROM deals AS d
WHERE d.status = 'closed_won'
GROUP BY d.account_id
"""
STATUS_ANN = [
    {
        "name": "status",
        "type": "text",
        "role": "fixed",
        "literals": ["main.L1"],
        "description": "Won deals only.",
    }
]


@pytest.fixture(scope="module")
def warehouse_copy(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("wh") / "warehouse.duckdb"
    shutil.copy(EXAMPLES / "warehouse.duckdb", path)
    return path


def _open(warehouse: Path) -> AppContext:
    profile = WarehouseProfile(
        name="demo", dialect=Dialect.DUCKDB, options={"path": str(warehouse)}
    )
    return open_context(AppConfig(db_url="sqlite://"), profiles=[profile], clock=lambda: FIXED)


@pytest.fixture
def ctx(warehouse_copy: Path) -> Iterator[AppContext]:
    with _open(warehouse_copy) as c:
        yield c


def _example(ctx: AppContext, name: str, user: User = ALICE, **meta: Any):
    sql = (EXAMPLES / "queries" / f"{name}.sql").read_text()
    ann = yaml.safe_load((EXAMPLES / "definitions" / f"{name}.annotations.yml").read_text())
    return propose_definition(
        ctx,
        user,
        sql=sql,
        dialect="duckdb",
        metadata={k: ann[k] for k in META_KEYS} | meta,
        annotations=ann["variables"],
    )


def _region(ctx: AppContext, sql: str = REGION_SQL):
    return propose_definition(
        ctx,
        ALICE,
        sql=sql,
        dialect=Dialect.DUCKDB,
        metadata={"id": "region_bookings", "title": "Region bookings"},
        annotations=STATUS_ANN,
    )


def _usage(ctx: AppContext) -> dict[str, int]:
    return {e.id: e.usage_count for e in ctx.store.list_joins()}


# ── join mining ───────────────────────────────────────────────────────────────────────

AD = "accounts__deals__account_id=account_id"
DI = "deals__invoices__deal_id=deal_id"


def test_cte_joins_are_mined_through_pass_through_keys(ctx: AppContext) -> None:
    # realization_rate joins only CTEs, but their key columns pass through from deals,
    # accounts and invoices, so the base-table edges are mined.
    _example(ctx, "realization_rate")
    assert _usage(ctx) == {AD: 1, DI: 1}
    ad, di = ctx.store.get_join(AD), ctx.store.get_join(DI)
    assert (ad.left_table, ad.right_table, ad.join_type) == ("accounts", "deals", "inner")
    assert (di.left_table, di.right_table, di.join_type) == ("deals", "invoices", "left")
    # Mined edges are known automatically (ADR 0026) and name their sources.
    assert ad.is_known() and di.is_known() and ad.sources == ["realization_rate"]
    _example(ctx, "booked_vs_collected")  # same two edges, now used by two definitions
    assert _usage(ctx) == {AD: 2, DI: 2}
    assert ctx.store.get_join(AD).sources == ["booked_vs_collected", "realization_rate"]


def test_computed_cte_keys_are_not_mined(ctx: AppContext) -> None:
    sql = """
    WITH acc AS (SELECT a.account_id + 0 AS account_id, a.region FROM accounts AS a)
    SELECT acc.region, SUM(d.amount) AS booked
    FROM deals AS d
    JOIN acc ON acc.account_id = d.account_id
    WHERE d.status = 'closed_won'
    GROUP BY acc.region
    """
    out = _region(ctx, sql)
    assert out.auto_applied
    assert ctx.store.list_joins() == []


def test_mined_usage_counts_are_recomputed(ctx: AppContext) -> None:
    _example(ctx, "booked_vs_collected")  # joins deals-accounts twice, invoices-deals once
    assert _usage(ctx) == {AD: 1, DI: 1}
    edge = ctx.store.get_join(AD)
    assert edge.left_table == "accounts" and edge.right_table == "deals"
    assert edge.is_known() and edge.join_type == "inner"
    _region(ctx)
    assert _usage(ctx) == {AD: 2, DI: 1}
    revise_definition(ctx, ALICE, id="region_bookings", base_version=1, sql=NO_JOIN_SQL)
    assert _usage(ctx) == {AD: 1, DI: 1}
    revise_definition(ctx, ALICE, id="region_bookings", base_version=2, metadata={"title": "x"})
    assert _usage(ctx) == {AD: 1, DI: 1}


# ── propose_join (ADR 0026: add | annotate) ───────────────────────────────────────────


def test_person_adds_an_edge_normalized_and_auto_applied(ctx: AppContext) -> None:
    out = propose_join(
        ctx,
        ALICE,
        action="add",
        left_table="Deals",
        right_table="accounts",
        keys=[("Account_ID", "account_id")],
        join_type="left",
        relationship="many_to_one",
        traps=["closed deals only"],
        notes="see the CRM docs",
        summary="deal → account",
    )
    assert out.auto_applied and out.proposal.kind is ProposalKind.NEW_JOIN
    assert out.proposal.payload["action"] == "add"
    edge = ctx.store.get_join(AD)
    assert edge.left_table == "accounts" and edge.right_table == "deals"
    assert edge.join_type == "right" and edge.relationship == "one_to_many"
    assert edge.traps == ["closed deals only"] and edge.notes == "see the CRM docs"
    assert edge.usage_count == 0 and edge.added_by == "alice" and edge.is_known()
    # The relationship was checked on the warehouse; the evidence is stored.
    evidence = out.proposal.payload["evidence"]
    assert evidence["observed"] == "one_to_many" and evidence["consistent"] is True
    assert evidence["left"]["rows"] == evidence["left"]["distinct_keys"]  # accounts: unique
    assert edge.evidence is not None and edge.evidence.claimed == "one_to_many"
    _example(ctx, "booked_vs_collected")
    edge = ctx.store.get_join(AD)
    assert edge.usage_count == 1 and edge.relationship == "one_to_many"  # mining keeps it
    assert edge.sources == ["booked_vs_collected"]


def test_agent_added_edge_never_auto_applies_and_is_known_only_after_a_person(
    ctx: AppContext,
) -> None:
    out = propose_join(
        ctx, BOT, action="add", left_table="deals", right_table="invoices", keys=[("deal_id",) * 2]
    )
    assert out.issues == [] and not out.auto_applied  # open mode, checks pass: still waits
    assert out.proposal.status is ProposalStatus.PENDING and ctx.store.get_join(DI) is None
    # An agent approver's decision does not make it known.
    review.approve(ctx, AGENT_APPROVER, out.proposal.id)
    edge = ctx.store.get_join(DI)
    assert edge.added_by == "bot" and edge.added_by_kind == "agent"
    assert edge.confirmed_by is None and not edge.is_known()
    again = propose_join(ctx, BOT, action="annotate", id=DI, notes="person please confirm")
    assert again.auto_applied  # an annotation of an unconfirmed edge follows the usual rule
    note = propose_join(ctx, BOT, action="annotate", id=DI, traps=["many invoices per deal"])
    assert note.auto_applied
    set_team_mode(ctx, TeamMode.STRICT)
    waits = propose_join(ctx, BOT, action="annotate", id=DI, notes="x")
    review.approve(ctx, APPROVER, waits.proposal.id)  # a person approves: confirmed, known
    edge = ctx.store.get_join(DI)
    assert edge.confirmed_by == "ann" and edge.is_known() and edge.notes == "x"
    assert edge.traps == ["many invoices per deal"]


def test_annotate_by_tables_and_keys_flips_relationship(ctx: AppContext) -> None:
    _example(ctx, "booked_vs_collected")
    out = propose_join(
        ctx,
        ALICE,
        action="annotate",
        left_table="invoices",
        right_table="deals",
        keys=[{"left": "deal_id", "right": "deal_id"}],
        relationship="many_to_one",
    )
    assert out.auto_applied and out.proposal.payload["action"] == "annotate"
    edge = ctx.store.get_join(DI)
    assert edge.usage_count == 1 and edge.relationship == "one_to_many"
    assert edge.sources == ["booked_vs_collected"]


def test_relationship_claim_contradicted_by_the_data(ctx: AppContext) -> None:
    _example(ctx, "booked_vs_collected")
    out = propose_join(ctx, ALICE, action="annotate", id=DI, relationship="one_to_one")
    # invoices has several rows per deal: the "one" right side has duplicate keys.
    assert [i.code for i in out.issues] == [IssueCode.RELATIONSHIP_CONTRADICTED]
    assert not out.auto_applied and out.proposal.status is ProposalStatus.PENDING
    evidence = out.proposal.payload["evidence"]
    assert evidence["consistent"] is False and evidence["observed"] == "one_to_many"
    assert evidence["right"]["rows"] > evidence["right"]["distinct_keys"]
    with pytest.raises(PolicyError, match="failing checks"):
        review.approve(ctx, APPROVER, out.proposal.id)
    assert ctx.store.get_join(DI).relationship is None


def test_annotate_and_add_refusals(ctx: AppContext) -> None:
    from cteforge.domain.errors import NotFoundError

    with pytest.raises(NotFoundError):
        propose_join(ctx, ALICE, action="annotate", id="nope", notes="x")
    with pytest.raises(NotFoundError, match="action='add'"):
        propose_join(
            ctx,
            ALICE,
            action="annotate",
            left_table="deals",
            right_table="accounts",
            keys=[("account_id",) * 2],
            notes="x",
        )
    _example(ctx, "booked_vs_collected")
    with pytest.raises(PolicyError, match="annotate"):
        propose_join(
            ctx,
            ALICE,
            action="add",
            left_table="deals",
            right_table="accounts",
            keys=[("account_id",) * 2],
        )
    with pytest.raises(PolicyError, match="Nothing to annotate"):
        propose_join(ctx, ALICE, action="annotate", id=AD)
    with pytest.raises(CodecError):
        propose_join(ctx, ALICE, action="remove", id=AD)


def test_confirmed_join_changes_wait_for_a_person(ctx: AppContext) -> None:
    set_team_mode(ctx, TeamMode.STRICT)
    first = propose_join(
        ctx,
        BOT,
        action="add",
        left_table="deals",
        right_table="invoices",
        keys=[("deal_id", "deal_id")],
    )
    assert first.proposal.status is ProposalStatus.PENDING and ctx.store.get_join(DI) is None
    with pytest.raises(PolicyError, match="not verified"):
        review.approve(ctx, APPROVER, first.proposal.id, verify=True)
    review.approve(ctx, APPROVER, first.proposal.id)
    assert ctx.store.get_join(DI).confirmed_by == "ann"
    set_team_mode(ctx, TeamMode.OPEN)
    change = propose_join(ctx, BOT, action="annotate", id=DI, traps=["x"])
    assert change.proposal.status is ProposalStatus.PENDING  # confirmed: waits
    with pytest.raises(PermissionDeniedError):
        review.approve(ctx, AGENT_APPROVER, change.proposal.id)
    with pytest.raises(PermissionDeniedError):
        review.approve(ctx, ALICE, change.proposal.id)
    review.approve(ctx, APPROVER, change.proposal.id)
    edge = ctx.store.get_join(DI)
    assert edge.traps == ["x"] and edge.confirmed_by == "ann"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"keys": []},
        {"keys": [("a", "")]},
        {"join_type": "cross"},
        {"relationship": "several"},
        {"left_table": " "},
    ],
)
def test_bad_join_input(ctx: AppContext, kwargs: dict[str, Any]) -> None:
    args = {"left_table": "deals", "right_table": "accounts", "keys": [("a", "b")]} | kwargs
    with pytest.raises(CodecError):
        propose_join(ctx, ALICE, action="add", **args)
    assert ctx.store.list_proposals() == []


# ── composed definitions in the library files ─────────────────────────────────────────

COMPOSITION = {
    "uses": {"rr": {"id": "realization_rate"}},
    "select": "SELECT rr.month, rr.realization_rate FROM rr",
    "fixed": {"rr": {"regions": ["EMEA"]}},
}


@pytest.fixture
def with_rr(ctx: AppContext) -> AppContext:
    _example(ctx, "realization_rate", join_keys=["month", "region"])
    return ctx


# ── library export / import ───────────────────────────────────────────────────────────


def _files(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): p.read_text() for p in sorted(root.rglob("*.yml"))}


def _populate(ctx: AppContext) -> None:
    _example(ctx, "realization_rate", join_keys=["month", "region"])
    _example(ctx, "booked_vs_collected")
    propose_join(
        ctx,
        ALICE,
        action="annotate",
        left_table="invoices",
        right_table="deals",
        keys=[("deal_id", "deal_id")],
        relationship="many_to_one",
        traps=["one deal, many invoices"],
    )
    propose_definition(
        ctx,
        ALICE,
        composition=COMPOSITION,
        dialect="duckdb",
        metadata={"id": "rr_emea", "title": "Realization EMEA"},
    )


def test_export_is_deterministic_and_complete(ctx: AppContext, tmp_path: Path) -> None:
    _populate(ctx)
    written = export_library(ctx, VIEWER, tmp_path / "a")
    export_library(ctx, VIEWER, tmp_path / "b")
    files = _files(tmp_path / "a")
    assert sorted(files) == [
        "definitions/booked_vs_collected.yml",
        "definitions/realization_rate.yml",
        "definitions/rr_emea.yml",
        f"joins/{AD}.yml",
        f"joins/{DI}.yml",
    ]
    assert len(written) == 5 and files == _files(tmp_path / "b")
    composed = yaml.safe_load(files["definitions/rr_emea.yml"])
    assert composed["schema_version"] == 2
    assert composed["composition"]["uses"] == {"rr": {"id": "realization_rate"}}
    assert composed["composition"]["fixed"] == {"rr": {"regions": ["EMEA"]}}
    join = yaml.safe_load(files[f"joins/{DI}.yml"])
    # Both definitions use deals<->invoices (realization_rate through its CTEs).
    assert join["relationship"] == "one_to_many" and join["usage_count"] == 3
    assert join["sources"] == ["booked_vs_collected", "realization_rate", "rr_emea"]


def test_export_prune_removes_stale_files(ctx: AppContext, tmp_path: Path) -> None:
    _populate(ctx)
    stale = tmp_path / "definitions" / "gone.yml"
    stale.parent.mkdir(parents=True)
    stale.write_text("x")
    keep = tmp_path / "notes.yml"
    keep.write_text("mine")
    legacy = tmp_path / "recipes" / "old.yml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("x")
    export_library(ctx, VIEWER, tmp_path)
    assert stale.exists() and legacy.exists()
    export_library(ctx, VIEWER, tmp_path, prune=True)
    assert not stale.exists() and not legacy.exists() and keep.exists()


def test_import_round_trip_goes_through_proposals(warehouse_copy: Path, tmp_path: Path) -> None:
    with _open(warehouse_copy) as source:
        _populate(source)
        export_library(source, VIEWER, tmp_path / "out")
    with _open(warehouse_copy) as target:
        outcomes = import_library(target, ALICE, tmp_path / "out")
        kinds = [o.proposal.kind for o in outcomes]
        assert kinds == [
            ProposalKind.NEW_DEFINITION,
            ProposalKind.NEW_DEFINITION,
            ProposalKind.NEW_JOIN,  # the annotation (the edge itself is mined)
            ProposalKind.NEW_DEFINITION,  # the composed one, after its part
        ]
        assert [o.proposal.payload.get("action") for o in outcomes][2] == "annotate"
        assert all(o.auto_applied for o in outcomes)
        assert target.store.get_definition("rr_emea").composition is not None
        assert {p.author for p in target.store.list_proposals()} == {"alice"}
        export_library(target, VIEWER, tmp_path / "again")
        assert _files(tmp_path / "again") == _files(tmp_path / "out")
        assert import_library(target, ALICE, tmp_path / "out") == []  # nothing changed


def test_import_in_strict_mode_writes_nothing(warehouse_copy: Path, tmp_path: Path) -> None:
    with _open(warehouse_copy) as source:
        _example(source, "realization_rate")
        export_library(source, VIEWER, tmp_path)
    with _open(warehouse_copy) as target:
        set_team_mode(target, TeamMode.STRICT)
        outcomes = import_library(target, ALICE, tmp_path)
        # The definition plus the two join edges mined from it in the source.
        assert [o.proposal.kind for o in outcomes] == [
            ProposalKind.NEW_DEFINITION,
            ProposalKind.NEW_JOIN,
            ProposalKind.NEW_JOIN,
        ]
        assert {o.proposal.status for o in outcomes} == {ProposalStatus.PENDING}
        assert target.store.list_definitions() == [] and target.store.list_joins() == []
        review.approve(target, APPROVER, outcomes[0].proposal.id)
        assert target.store.get_definition("realization_rate").version == 1


def test_import_of_changed_file_is_a_revision_and_drops_verification(
    ctx: AppContext, tmp_path: Path
) -> None:
    _example(ctx, "realization_rate")
    export_library(ctx, VIEWER, tmp_path)
    path = tmp_path / "definitions" / "realization_rate.yml"
    data = yaml.safe_load(path.read_text())
    data |= {"title": "Edited in git", "state": "verified", "verified_by": "mallory"}
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    [out] = import_library(ctx, ALICE, tmp_path)
    assert out.proposal.kind is ProposalKind.REVISE_DEFINITION and out.proposal.base_version == 1
    stored = ctx.store.get_definition("realization_rate")
    assert stored.version == 2 and stored.title == "Edited in git"
    assert stored.state is DefinitionState.LLM_ANNOTATED and stored.verified_by is None


def test_import_permissions_and_bad_files(ctx: AppContext, tmp_path: Path) -> None:
    with pytest.raises(PermissionDeniedError):
        import_library(ctx, VIEWER, tmp_path)
    with pytest.raises(CodecError):
        import_library(ctx, ALICE, tmp_path / "missing")
    (tmp_path / "recipes").mkdir()
    (tmp_path / "recipes" / "bad.yml").write_text("id: [unclosed")
    with pytest.raises(CodecError, match="bad.yml"):
        import_library(ctx, ALICE, tmp_path)


def test_old_recipe_files_import_as_composed_definitions(
    with_rr: AppContext, tmp_path: Path
) -> None:
    recipe = {
        "schema_version": 1,
        "id": "rr_emea",
        "version": 3,
        "title": "Realization EMEA",
        "description": "EMEA only",
        "uses": {"rr": {"id": "realization_rate"}},
        "values": {"rr": {"regions": ["EMEA"]}},
        "select": "SELECT rr.month, rr.realization_rate FROM rr",
        "dialect": "duckdb",
        "state": "verified",
    }
    (tmp_path / "recipes").mkdir()
    (tmp_path / "recipes" / "rr_emea.yml").write_text(yaml.safe_dump(recipe))
    [out] = import_library(with_rr, ALICE, tmp_path)
    assert out.proposal.kind is ProposalKind.NEW_DEFINITION and out.auto_applied
    stored = with_rr.store.get_definition("rr_emea")
    assert stored.composition is not None and stored.meaning == "EMEA only"
    assert stored.composition.fixed == {"rr": {"regions": ["EMEA"]}}
    assert stored.state is DefinitionState.LLM_ANNOTATED  # never verified by a file


# ── regressions (fixer pass) ──────────────────────────────────────────────────────────


def test_join_on_missing_table_or_column_is_an_error_and_never_applies(ctx: AppContext) -> None:
    """Regression: join proposals ran no checks, so any edge auto-applied in open mode."""
    out = propose_join(
        ctx,
        ALICE,
        action="add",
        left_table="no_such_table",
        right_table="deals",
        keys=[["nope", "deal_id"]],
    )
    assert not out.auto_applied and out.proposal.status is ProposalStatus.PENDING
    assert [(i.code, i.severity) for i in out.issues] == [(IssueCode.NOT_FOUND, Severity.ERROR)]
    assert ctx.store.list_joins() == []
    with pytest.raises(PolicyError):
        review.approve(ctx, APPROVER, out.proposal.id)
    bad_key = propose_join(
        ctx,
        ALICE,
        action="add",
        left_table="accounts",
        right_table="deals",
        keys=[["account_id", "nope"]],
    )
    assert [i.code for i in bad_key.issues] == [IssueCode.NOT_FOUND]
    assert "nope" in bad_key.issues[0].message and ctx.store.list_joins() == []


def test_join_without_a_warehouse_waits_for_review() -> None:
    with open_context(AppConfig(db_url="sqlite://"), clock=lambda: FIXED) as c:
        out = propose_join(
            c,
            ALICE,
            action="add",
            left_table="deals",
            right_table="accounts",
            keys=[["account_id"] * 2],
        )
        assert not out.auto_applied
        assert [i.code for i in out.issues] == [IssueCode.CHECKS_INCOMPLETE]
        assert c.store.list_joins() == []


def test_older_join_proposal_is_stale_after_the_edge_changed(ctx: AppContext) -> None:
    """Regression: approving an older join proposal silently overwrote (and un-verified)
    an edge that changed after it was proposed."""
    from cteforge.domain.errors import StaleVersionError

    set_team_mode(ctx, TeamMode.STRICT)
    keys = [["account_id", "account_id"]]
    p1 = propose_join(
        ctx,
        ALICE,
        action="add",
        left_table="deals",
        right_table="accounts",
        keys=keys,
        relationship="many_to_one",
    )
    p2 = propose_join(
        ctx,
        ALICE,
        action="add",
        left_table="deals",
        right_table="accounts",
        keys=keys,
        relationship="many_to_one",
        traps=["x"],
    )
    review.approve(ctx, APPROVER, p1.proposal.id)
    with pytest.raises(StaleVersionError) as info:
        review.approve(ctx, APPROVER, p2.proposal.id)
    assert [i.code for i in info.value.issues] == [IssueCode.STALE_BASE_VERSION]
    edge = ctx.store.get_join(AD)
    assert edge.confirmed_by == "ann" and edge.relationship == "one_to_many"
    assert edge.traps == []
    assert review.get_proposal(ctx, APPROVER, p2.proposal.id).status is ProposalStatus.PENDING


def test_failed_import_leaves_store_untouched(warehouse_copy: Path, tmp_path: Path) -> None:
    """Regression: an import that failed on a later file left earlier proposals applied
    with no success audit event."""
    with _open(warehouse_copy) as source:
        _example(source, "realization_rate")
        export_library(source, VIEWER, tmp_path)
    good = yaml.safe_load((tmp_path / "definitions" / "realization_rate.yml").read_text())
    other = good | {"id": "zz_other", "owner": "mallory"}
    (tmp_path / "definitions" / "zz_other.yml").write_text(yaml.safe_dump(other, sort_keys=False))
    with _open(warehouse_copy) as target:
        with pytest.raises(PermissionDeniedError):
            import_library(target, ALICE, tmp_path)
        assert target.store.list_definitions() == []
        assert target.store.list_proposals() == []
        events = target.audit.list_events(limit=10)
        assert [(e.kind, e.outcome.value, e.extra.get("op")) for e in events] == [
            ("propose", "rejected", "import_library")
        ]


def test_each_imported_proposal_is_audited_when_submitted(
    warehouse_copy: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If a later step fails, what was already applied still has its PROPOSE event."""
    from cteforge.services import _apply

    with _open(warehouse_copy) as source:
        _populate(source)
        export_library(source, VIEWER, tmp_path)
    with _open(warehouse_copy) as target:

        def boom(*_a: Any, **_k: Any) -> Any:
            raise CodecError("composed step failed")

        monkeypatch.setattr(_apply, "checked_composition", boom)
        with pytest.raises(CodecError):
            import_library(target, ALICE, tmp_path)
        applied = {d.id for d in target.store.list_definitions()}
        assert applied  # definitions were applied before the failure ...
        audited = {
            e.extra.get("target_id")
            for e in target.audit.list_events(limit=50)
            if e.outcome.value == "ok" and e.extra.get("op") == "import_library"
        }
        assert applied <= audited  # ... and each has a success audit event
