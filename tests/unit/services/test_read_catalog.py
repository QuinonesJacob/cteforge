"""catalog: list_joins, describe_tables (discovery for agents and the GUI)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from cteforge.domain.audit import AuditKind
from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import PolicyError
from cteforge.domain.models import (
    DefinitionState,
    JoinEdge,
    JoinKey,
)
from cteforge.domain.users import ActorKind, User, UserRole
from cteforge.files.definitions import load_definition
from cteforge.services import catalog
from cteforge.services.cards import card_from_definition
from cteforge.services.context import AppConfig, AppContext, open_context

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"
AGENT = User(name="claude-agent", role=UserRole.VIEWER, kind=ActorKind.AGENT)


@pytest.fixture(scope="module")
def wh_file(warehouse_path: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("catalog_wh") / "warehouse.duckdb"
    shutil.copy(warehouse_path, path)
    return path


@pytest.fixture
def ctx(tmp_path: Path, wh_file: Path):
    profile = WarehouseProfile(name="demo", dialect=Dialect.DUCKDB, options={"path": str(wh_file)})
    config = AppConfig(db_url=f"sqlite:///{tmp_path / 'lib.db'}")
    with open_context(config, profiles=[profile]) as context:
        rr = load_definition(EXAMPLES / "definitions" / "realization_rate.yml")
        context.store.add_definition_version(
            rr.model_copy(update={"state": DefinitionState.VERIFIED}), author="analyst"
        )
        yield context


def _edge(**fields: object) -> JoinEdge:
    return JoinEdge(
        id="accounts__deals__account_id=account_id",
        left_table="accounts",
        right_table="deals",
        keys=[JoinKey(left="account_id", right="account_id")],
        traps=["one account has many deals"],
        **fields,
    )


@pytest.mark.usefixtures("mcp_surface")  # reads are logged through MCP (ADR 0029)
def test_list_joins_by_table_and_known(ctx: AppContext) -> None:
    ctx.store.upsert_join(_edge(added_by="claude-agent", added_by_kind="agent"))
    (card,) = catalog.list_joins(ctx, AGENT)
    assert card.id == _edge().id and card.traps == ["one account has many deals"]
    assert not card.known and not card.confirmed  # an agent's edge, nobody approved it
    assert [e.id for e in catalog.list_joins(ctx, AGENT, table="main.deals")] == [_edge().id]
    assert catalog.list_joins(ctx, AGENT, table="invoices") == []
    assert catalog.list_joins(ctx, AGENT, known_only=True) == []
    ctx.store.upsert_join(_edge(sources=["realization_rate"], usage_count=1))
    # Mined only from definitions no person stands behind: listed, not known (ADR 0026).
    assert catalog.list_joins(ctx, AGENT, known_only=True) == []
    ctx.store.upsert_join(
        _edge(sources=["realization_rate"], trusted_sources=["realization_rate"], usage_count=1)
    )
    (mined,) = catalog.list_joins(ctx, AGENT, known_only=True)
    # Mined from a verified definition: known and confirmed.
    assert mined.known and mined.confirmed and mined.sources == ["realization_rate"]
    assert [e.id for e in catalog.list_joins(ctx, AGENT, definition="realization_rate")] == [
        _edge().id
    ]
    assert catalog.list_joins(ctx, AGENT, definition="other") == []
    event = ctx.audit.list_events(limit=1)[0]
    assert event.kind is AuditKind.GET_DEFINITION and event.extra["op"] == "list_joins"


def test_list_joins_pending_proposals_only_for_proposal_readers(ctx: AppContext) -> None:
    from cteforge.domain.access import Tool
    from cteforge.services.propose_other import propose_join

    ctx.store.upsert_join(_edge(sources=["realization_rate"], usage_count=1))
    set_mode_strict(ctx)
    contributor = User(name="carl", role=UserRole.CONTRIBUTOR, kind=ActorKind.HUMAN)
    outcome = propose_join(ctx, contributor, action="annotate", id=_edge().id, notes="see wiki")
    assert outcome.proposal.status == "pending"
    (card,) = catalog.list_joins(ctx, AGENT)
    assert card.pending_proposals == [outcome.proposal.id]
    narrow = AGENT.model_copy(update={"tools": frozenset({Tool.LIST_JOINS})})
    (hidden,) = catalog.list_joins(ctx, narrow)
    assert hidden.pending_proposals == []


def set_mode_strict(ctx: AppContext) -> None:
    from cteforge.domain.models import TeamMode
    from cteforge.services.context import set_team_mode

    set_team_mode(ctx, TeamMode.STRICT)


def test_describe_tables_lists_the_library_tables_with_columns(ctx: AppContext) -> None:
    ctx.store.upsert_join(_edge())
    cards = {c.name: c for c in catalog.describe_tables(ctx, AGENT)}
    assert set(cards) == {"accounts", "deals", "invoices"}
    deals = cards["deals"]
    assert deals.found
    assert {c.name: c.type for c in deals.columns}["closed_at"] == "TIMESTAMP"
    assert "amount" in {c.name for c in deals.columns}
    assert deals.used_by == ["realization_rate"]
    assert deals.joins == [_edge().id]


def test_describe_tables_by_name_and_unknown_tables(ctx: AppContext) -> None:
    (card,) = catalog.describe_tables(ctx, AGENT, ["no_such_table"])
    assert not card.found and card.columns == [] and card.used_by == []
    with pytest.raises(PolicyError):
        catalog.describe_tables(ctx, AGENT, [f"t{n}" for n in range(catalog.MAX_TABLES + 1)])


def test_describe_tables_reports_units_declared_by_variables(ctx: AppContext) -> None:
    bvc = load_definition(EXAMPLES / "definitions" / "booked_vs_collected.yml")
    with_unit = [v for v in bvc.variables if v.unit and v.column]
    if not with_unit:
        pytest.skip("example has no variable with a unit and a column")
    ctx.store.add_definition_version(bvc, author="analyst")
    table, column = with_unit[0].column.rsplit(".", 1)
    (card,) = catalog.describe_tables(ctx, AGENT, [table])
    assert card.units.get(column.lower()) == with_unit[0].unit


def test_card_shows_how_each_variable_is_compared() -> None:
    rr = load_definition(EXAMPLES / "definitions" / "realization_rate.yml")
    comparisons = {v.name: v.comparisons for v in card_from_definition(rr).variables}
    assert comparisons["period"] == [">=", "<"]
    assert comparisons["regions"] == ["IN"]
