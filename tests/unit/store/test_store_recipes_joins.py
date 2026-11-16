from __future__ import annotations

import pytest

from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import StaleVersionError
from cteforge.domain.models import DefinitionRef, JoinEdge, JoinKey, Recipe
from cteforge.store import Store


def recipe(id: str, version: int = 1, uses: dict[str, str] | None = None) -> Recipe:
    uses = uses if uses is not None else {"rr": "realization_rate"}
    return Recipe(
        id=id,
        version=version,
        title=f"{id} v{version}",
        uses={alias: DefinitionRef(id=def_id) for alias, def_id in uses.items()},
        values={alias: {"regions": ["EMEA", "NA"]} for alias in uses},
        select="SELECT * FROM rr",
        dialect=Dialect.DUCKDB,
    )


def edge(left: str, right: str, usage: int = 1) -> JoinEdge:
    return JoinEdge(
        id=f"{left}__{right}__k".lower(),
        left_table=left,
        right_table=right,
        keys=[JoinKey(left="account_id", right="account_id")],
        relationship="many_to_one",
        usage_count=usage,
        traps=["fan-out: one deal has many invoices"],
    )


# --------------------------------------------------------------------- recipes


def test_recipe_round_trip_and_versions(store: Store) -> None:
    r1 = recipe("emea_rr")
    pinned = Recipe.model_validate(
        recipe("emea_rr", 2).model_dump()
        | {"uses": {"rr": {"id": "realization_rate", "version": 1}}}
    )
    store.add_recipe_version(r1, author="jq")
    store.add_recipe_version(pinned, author="ann")
    assert store.get_recipe("emea_rr") == pinned
    assert store.get_recipe("emea_rr", 1) == r1
    assert store.get_recipe("emea_rr", 3) is None
    assert store.get_recipe("nope") is None


@pytest.mark.parametrize("bad_version", [1, 3])
def test_recipe_version_rule(store: Store, bad_version: int) -> None:
    store.add_recipe_version(recipe("r"), author="jq")
    with pytest.raises(StaleVersionError):
        store.add_recipe_version(recipe("r", bad_version), author="jq")
    with pytest.raises(StaleVersionError):
        store.add_recipe_version(recipe("new", 2), author="jq")
    assert store.get_recipe("r", 1) is not None
    assert store.get_recipe("new") is None


def test_list_recipes_returns_latest_sorted(store: Store) -> None:
    store.add_recipe_version(recipe("b"), author="jq")
    store.add_recipe_version(recipe("a"), author="jq")
    store.add_recipe_version(recipe("a", 2), author="jq")
    assert [(r.id, r.version) for r in store.list_recipes()] == [("a", 2), ("b", 1)]


def test_recipes_using_checks_latest_versions_only(store: Store) -> None:
    store.add_recipe_version(recipe("uses_rr"), author="jq")
    store.add_recipe_version(recipe("other", uses={"x": "pipeline"}), author="jq")
    store.add_recipe_version(recipe("dropped_rr"), author="jq")
    store.add_recipe_version(recipe("dropped_rr", 2, uses={"x": "pipeline"}), author="jq")
    store.add_recipe_version(
        recipe("both", uses={"x": "pipeline", "rr": "realization_rate"}), author="jq"
    )
    assert [r.id for r in store.recipes_using("realization_rate")] == ["both", "uses_rr"]
    assert [r.id for r in store.recipes_using("pipeline")] == ["both", "dropped_rr", "other"]
    assert store.recipes_using("unused") == []


# -------------------------------------------------------------------- join map


def test_upsert_join_inserts_then_replaces(store: Store) -> None:
    first = edge("deals", "accounts", usage=1)
    assert store.upsert_join(first) == first
    assert store.get_join(first.id) == first
    replaced = edge("deals", "accounts", usage=7)
    store.upsert_join(replaced)
    assert store.get_join(first.id).usage_count == 7
    assert len(store.list_joins()) == 1


def test_get_missing_join_returns_none(store: Store) -> None:
    assert store.get_join("nope") is None


def test_list_joins_matches_either_side_case_insensitively(store: Store) -> None:
    store.upsert_join(edge("Deals", "accounts"))
    store.upsert_join(edge("invoices", "DEALS"))
    store.upsert_join(edge("accounts", "regions"))
    assert [j.id for j in store.list_joins()] == [
        "accounts__regions__k",
        "deals__accounts__k",
        "invoices__deals__k",
    ]
    assert [j.id for j in store.list_joins("deals")] == ["deals__accounts__k", "invoices__deals__k"]
    assert [j.id for j in store.list_joins("ACCOUNTS")] == [
        "accounts__regions__k",
        "deals__accounts__k",
    ]
    assert store.list_joins("payments") == []
    # The model keeps its original casing; only the index is lowercased.
    assert store.get_join("deals__accounts__k").left_table == "Deals"
