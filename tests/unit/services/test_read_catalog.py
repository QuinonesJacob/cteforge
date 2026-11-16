"""catalog: list_recipes, list_joins, describe_tables (discovery for agents and the GUI)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from cteforge.domain.audit import AuditKind
from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import PolicyError
from cteforge.domain.models import (
    DefinitionRef,
    DefinitionState,
    JoinEdge,
    JoinKey,
    Recipe,
    TeamMode,
)
from cteforge.domain.users import ActorKind, User, UserRole
from cteforge.files.definitions import load_definition
from cteforge.services import catalog
from cteforge.services.cards import card_from_definition
from cteforge.services.context import AppConfig, AppContext, open_context, set_team_mode

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


def _recipe(id: str, state: DefinitionState, title: str = "APAC Q3 2025 realization") -> Recipe:
    return Recipe(
        id=id,
        title=title,
        uses={"rr": DefinitionRef(id="realization_rate")},
        values={"rr": {"regions": ["APAC"]}},
        dialect=Dialect.DUCKDB,
        state=state,
    )


def _edge(state: DefinitionState = DefinitionState.LLM_ANNOTATED) -> JoinEdge:
    return JoinEdge(
        id="accounts__deals__account_id=account_id",
        left_table="accounts",
        right_table="deals",
        keys=[JoinKey(left="account_id", right="account_id")],
        traps=["one account has many deals"],
        state=state,
    )


def test_list_recipes_filters_by_words_and_hides_drafts(ctx: AppContext) -> None:
    ctx.store.add_recipe_version(_recipe("apac_q3_rr", DefinitionState.LLM_ANNOTATED), author="a")
    ctx.store.add_recipe_version(_recipe("wip", DefinitionState.DRAFT, "Draft thing"), author="a")
    assert [r.id for r in catalog.list_recipes(ctx, AGENT)] == ["apac_q3_rr"]
    assert [r.id for r in catalog.list_recipes(ctx, AGENT, query="APAC realization")] == [
        "apac_q3_rr"
    ]
    assert catalog.list_recipes(ctx, AGENT, query="forecast") == []
    every = catalog.list_recipes(ctx, AGENT, include_drafts=True)
    assert {r.id for r in every} == {"apac_q3_rr", "wip"}
    set_team_mode(ctx, TeamMode.STRICT)
    assert catalog.list_recipes(ctx, AGENT) == []  # only verified recipes are usable
    event = ctx.audit.list_events(limit=1)[0]
    assert event.kind is AuditKind.GET_DEFINITION and event.extra["op"] == "list_recipes"


def test_list_joins_by_table_and_state(ctx: AppContext) -> None:
    ctx.store.upsert_join(_edge())
    assert [e.id for e in catalog.list_joins(ctx, AGENT)] == [_edge().id]
    assert [e.id for e in catalog.list_joins(ctx, AGENT, table="main.deals")] == [_edge().id]
    assert catalog.list_joins(ctx, AGENT, table="invoices") == []
    ctx.store.upsert_join(_edge(DefinitionState.DRAFT))
    assert catalog.list_joins(ctx, AGENT) == []
    assert len(catalog.list_joins(ctx, AGENT, include_drafts=True)) == 1


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
