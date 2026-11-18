"""Join map storage, composed definitions using a part, and the schema-3 migration
(ADR 0026: recipes → composed definitions, recipe proposals withdrawn, removed tools
stripped from roles and tokens, join edges' old `state`)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from sqlalchemy import create_engine, insert, select, update

from cteforge.domain.access import Role, TokenInfo, Tool
from cteforge.domain.dialects import Dialect
from cteforge.domain.models import (
    Composition,
    Definition,
    DefinitionRef,
    DefinitionState,
    JoinEdge,
    JoinKey,
)
from cteforge.domain.proposals import Proposal, ProposalKind, ProposalStatus
from cteforge.domain.users import ActorKind, User
from cteforge.store import Store
from cteforge.store import tables as t
from cteforge.store.store import MIGRATION_NOTES_SETTING

NOW = dt.datetime(2026, 11, 1, 12, 0, tzinfo=dt.UTC)


def edge(left: str, right: str, usage: int = 1, **fields: object) -> JoinEdge:
    return JoinEdge(
        id=f"{left}__{right}__k".lower(),
        left_table=left,
        right_table=right,
        keys=[JoinKey(left="account_id", right="account_id")],
        relationship="many_to_one",
        usage_count=usage,
        traps=["fan-out: one deal has many invoices"],
        **fields,
    )


# -------------------------------------------------------------------- join map


def test_upsert_join_inserts_then_replaces(store: Store) -> None:
    first = edge("deals", "accounts", usage=1, notes="n", sources=["rr"])
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
    assert store.list_joins("payments") == []
    assert store.get_join("deals__accounts__k").left_table == "Deals"


def test_edge_known_rules() -> None:
    assert edge("a", "b", sources=["rr"]).is_known()  # mined from a definition
    assert edge("a", "b", added_by="ann", added_by_kind="human").is_known()
    assert not edge("a", "b", added_by="bot", added_by_kind="agent").is_known()
    assert edge("a", "b", added_by="bot", added_by_kind="agent", confirmed_by="ann").is_known()


def test_legacy_edge_state_is_dropped_on_read() -> None:
    legacy = edge("a", "b").model_dump(mode="json") | {"state": "verified"}
    assert JoinEdge.model_validate(legacy).id == "a__b__k"


# ------------------------------------------------------- composed definitions


def composed(id: str, *parts: str, pinned: int | None = None) -> Definition:
    return Definition(
        id=id,
        title=id,
        source_dialect=Dialect.DUCKDB,
        template="SELECT * FROM a" if len(parts) == 1 else "SELECT * FROM a, b",
        composition=Composition(
            uses={
                alias: DefinitionRef(id=part, version=pinned)
                for alias, part in zip(("a", "b"), parts, strict=False)
            }
        ),
    )


def test_definitions_using_lists_latest_composed_versions(store: Store, make_def) -> None:
    store.add_definition_version(make_def("rr"), author="jq")
    store.add_definition_version(make_def("other"), author="jq")
    store.add_definition_version(composed("uses_rr", "rr"), author="jq")
    store.add_definition_version(composed("both", "rr", "other"), author="jq")
    dropped = composed("dropped", "rr")
    store.add_definition_version(dropped, author="jq")
    store.add_definition_version(
        composed("dropped", "other").model_copy(update={"version": 2}), author="jq"
    )
    assert [d.id for d in store.definitions_using("rr")] == ["both", "uses_rr"]
    assert [d.id for d in store.definitions_using("other")] == ["both", "dropped"]
    assert store.definitions_using("unused") == []


def test_rename_updates_every_version_of_composed_definitions(store: Store, make_def) -> None:
    store.add_definition_version(make_def("rr"), author="jq")
    store.add_definition_version(composed("c", "rr", pinned=1), author="jq")
    store.add_definition_version(composed("c", "rr").model_copy(update={"version": 2}), author="jq")
    store.rename_definition("rr", "realization", renamed_by="jq")
    for version, pinned in ((1, 1), (2, None)):
        stored = store.get_definition("c", version)
        assert stored.composition is not None
        assert stored.composition.uses["a"] == DefinitionRef(id="realization", version=pinned)
    assert [d.id for d in store.definitions_using("realization")] == ["c"]
    assert store.definitions_using("rr") == []


# ------------------------------------------------------- migration 2 → 3


def _recipe(id: str, state: str, **extra: object) -> dict:
    return {
        "schema_version": 1,
        "id": id,
        "version": 1,
        "title": f"Recipe {id}",
        "description": "APAC realization",
        "uses": {"rr": {"id": "realization_rate"}},
        "values": {"rr": {"regions": ["APAC"]}},
        "select": "SELECT * FROM rr",
        "dialect": "duckdb",
        "state": state,
    } | extra


def _proposal(id: str, kind: ProposalKind, target: str, status: ProposalStatus, **f) -> Proposal:
    return Proposal(
        id=id,
        kind=kind,
        target_id=target,
        payload=f.pop("payload", {}),
        author=f.pop("author", "bot"),
        author_kind=f.pop("author_kind", ActorKind.AGENT),
        created_at=NOW,
        status=status,
        **f,
    )


def _v2_database(path: Path, make_def) -> str:
    """A database as cteforge with STORE_SCHEMA_VERSION 2 left it: recipes, recipe
    proposals, roles/tokens listing the recipe tools, edges with `state`."""
    url = f"sqlite:///{path}"
    s = Store(url)
    s.init_schema()
    s.add_definition_version(make_def("realization_rate"), author="jq")
    s.add_definition_version(make_def("taken"), author="jq")  # a recipe id already in use
    s.add_user(User(name="ann", role="approver", kind=ActorKind.HUMAN))
    s.add_token(
        TokenInfo(
            id="tok1",
            user_name="ann",
            name="cli",
            prefix="cfg_pat_abcd",
            created_at=NOW,
            created_by="ann",
        ),
        "a" * 64,
    )
    s.add_role(Role(name="bot_role", allowed=frozenset({Tool.SEARCH})))
    proposals = [
        _proposal(
            "p_verified",
            ProposalKind.NEW_RECIPE,
            "apac_rr",
            ProposalStatus.APPROVED,
            decided_by="ann",
            decided_by_kind=ActorKind.HUMAN,
            decided_at=NOW,
            resulting_version=1,
            payload={"recipe": _recipe("apac_rr", "verified")},
        ),
        _proposal(
            "p_pending",
            ProposalKind.NEW_RECIPE,
            "wip",
            ProposalStatus.PENDING,
            payload={"recipe": _recipe("wip", "llm_annotated")},
        ),
        _proposal(
            "p_join",
            ProposalKind.NEW_JOIN,
            "a__b__k",
            ProposalStatus.APPROVED,
            author="bot",
            decided_by="ann",
            decided_by_kind=ActorKind.HUMAN,
            payload={"join": {}},
        ),
    ]
    for p in proposals:
        s.add_proposal(p)
    s.close()
    engine = create_engine(url)
    with engine.begin() as conn:
        for recipe in (_recipe("apac_rr", "verified"), _recipe("taken", "llm_annotated")):
            conn.execute(
                insert(t.recipes).values(id=recipe["id"], latest_version=1, updated_at=NOW)
            )
            conn.execute(
                insert(t.recipe_versions).values(
                    recipe_id=recipe["id"],
                    version=1,
                    data=json.dumps(recipe),
                    author="bot",
                    created_at=NOW,
                )
            )
        conn.execute(
            update(t.roles)
            .where(t.roles.c.name == "contributor")
            .values(
                allowed=json.dumps(["search", "list_recipes", "propose_recipe"]),
                defaults=json.dumps(["search", "list_recipes"]),
            )
        )
        conn.execute(
            update(t.tokens)
            .where(t.tokens.c.id == "tok1")
            .values(
                include_tools=json.dumps(["list_recipes"]),
                exclude_tools=json.dumps(["propose_recipe", "search"]),
            )
        )
        legacy = edge("a", "b").model_dump(mode="json") | {"state": "verified", "id": "a__b__k"}
        conn.execute(
            insert(t.join_edges).values(
                id="a__b__k", left_table="a", right_table="b", data=json.dumps(legacy)
            )
        )
        conn.execute(
            update(t.schema_meta).where(t.schema_meta.c.key == "schema_version").values(value="2")
        )
    engine.dispose()
    return url


def test_migration_turns_recipes_into_composed_definitions(tmp_path: Path, make_def) -> None:
    url = _v2_database(tmp_path / "old.db", make_def)
    store = Store(url)
    store.init_schema()
    verified = store.get_definition("apac_rr")
    assert verified is not None and verified.composition is not None
    assert verified.state is DefinitionState.VERIFIED and verified.verified_by == "ann"
    assert verified.composition.uses == {"rr": DefinitionRef(id="realization_rate")}
    assert verified.composition.fixed == {"rr": {"regions": ["APAC"]}}
    assert verified.template == "SELECT * FROM rr" and verified.meaning == "APAC realization"
    assert store.definition_history("apac_rr")[0].author == "bot"
    # The recipe id `taken` was in use: it became `taken_recipe`, llm_annotated.
    moved = store.get_definition("taken_recipe")
    assert moved is not None and moved.state is DefinitionState.LLM_ANNOTATED
    assert store.get_definition("taken").composition is None  # untouched
    # Pending recipe proposals are withdrawn with a comment; decided ones stay.
    pending = store.get_proposal("p_pending")
    assert pending.status is ProposalStatus.WITHDRAWN and "ADR 0026" in pending.comment
    assert store.get_proposal("p_verified").status is ProposalStatus.APPROVED
    # Removed tools are stripped from roles and tokens; the rest is kept.
    contributor = store.get_role("contributor")
    assert contributor.allowed == frozenset({Tool.SEARCH})
    engine = create_engine(url)
    with engine.connect() as conn:
        role_row = conn.execute(select(t.roles).where(t.roles.c.name == "contributor")).one()
        token_row = conn.execute(select(t.tokens).where(t.tokens.c.id == "tok1")).one()
        version = conn.execute(
            select(t.schema_meta.c.value).where(t.schema_meta.c.key == "schema_version")
        ).scalar_one()
    engine.dispose()
    assert json.loads(role_row.allowed) == ["search"]
    assert json.loads(role_row.defaults) == ["search"]
    assert json.loads(token_row.include_tools) == []
    assert json.loads(token_row.exclude_tools) == ["search"]
    assert version == str(t.STORE_SCHEMA_VERSION)
    # Join edges: the person who approved the join proposal confirmed it.
    joined = store.get_join("a__b__k")
    assert joined.confirmed_by == "ann" and joined.added_by == "bot"
    # A note for services to audit, kept until cleared.
    notes = store.migration_notes()
    assert notes is not None and notes["withdrawn_proposals"] == ["p_pending"]
    assert {"recipe": "taken", "definition": "taken_recipe", "state": "llm_annotated"} in notes[
        "recipes"
    ]
    assert notes["roles_tokens_changed"] == 2
    store.clear_migration_notes()
    assert store.migration_notes() is None
    assert store.get_setting(MIGRATION_NOTES_SETTING) is None
    # Opening again does nothing more.
    store.init_schema()
    assert store.migration_notes() is None
    assert len([d for d in store.list_definitions() if d.composition]) == 2


def test_verified_recipe_without_a_verifier_record_becomes_llm_annotated(
    tmp_path: Path, make_def
) -> None:
    url = _v2_database(tmp_path / "old.db", make_def)
    engine = create_engine(url)
    with engine.begin() as conn:  # the approving proposal is gone: nobody to credit
        conn.execute(t.proposals.delete().where(t.proposals.c.id == "p_verified"))
    engine.dispose()
    store = Store(url)
    store.init_schema()
    migrated = store.get_definition("apac_rr")
    assert migrated.state is DefinitionState.LLM_ANNOTATED and migrated.verified_by is None
