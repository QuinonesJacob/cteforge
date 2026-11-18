"""Store.rename_definition / former ids (ADR 0024): one transaction, every stored copy."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable

import pytest

from cteforge.domain.codec import definition_to_yaml
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import StoreError
from cteforge.domain.issues import Issue, IssueCode, Severity
from cteforge.domain.models import Composition, Definition, DefinitionRef
from cteforge.domain.proposals import Proposal, ProposalKind, ProposalStatus
from cteforge.domain.search import SearchVector
from cteforge.domain.users import ActorKind
from cteforge.store import Store

WHEN = dt.datetime(2026, 11, 16, 9, 30, tzinfo=dt.UTC)


def _composed(id: str, def_id: str, version: int | None = None) -> Definition:
    """A composed definition (ADR 0026) using `def_id` as part `a`."""
    return Definition(
        id=id,
        title=id,
        source_dialect=Dialect.DUCKDB,
        template="SELECT * FROM a, b",
        composition=Composition(
            uses={"a": DefinitionRef(id=def_id, version=version), "b": DefinitionRef(id="other")}
        ),
    )


def _proposal(id: str, kind: ProposalKind, target: str, payload: dict) -> Proposal:
    return Proposal(
        id=id,
        kind=kind,
        target_id=target,
        payload=payload,
        checks=[Issue(code=IssueCode.TYPE_UNKNOWN, severity=Severity.WARNING, message="m")],
        author="alice",
        author_kind=ActorKind.HUMAN,
        created_at=WHEN,
    )


@pytest.fixture
def filled(store: Store, make_def: Callable[..., Definition]) -> Store:
    store.add_definition_version(make_def("rate"), author="alice")
    store.add_definition_version(make_def("rate", 2), author="bob", logic_changed=False)
    store.add_definition_version(make_def("other"), author="alice")
    store.add_definition_version(_composed("r1", "rate"), author="alice")
    store.add_definition_version(
        _composed("r1", "rate", 1).model_copy(update={"version": 2}), author="a"
    )
    store.add_definition_version(_composed("r2", "other"), author="alice")
    revise = definition_to_yaml(make_def("rate", 3))
    store.add_proposal(
        _proposal("p1", ProposalKind.REVISE_DEFINITION, "rate", {"definition_yaml": revise})
    )
    composed_payload = {"definition_yaml": definition_to_yaml(_composed("r3", "rate"))}
    store.add_proposal(_proposal("p2", ProposalKind.NEW_DEFINITION, "r3", composed_payload))
    legacy = {"recipe": {"uses": {"a": {"id": "rate"}}}}
    store.add_proposal(_proposal("p5", ProposalKind.NEW_RECIPE, "r5", legacy))
    store.add_proposal(
        _proposal("p3", ProposalKind.RENAME_DEFINITION, "rate", {"from_id": "rate", "to_id": "x"})
    )
    store.add_proposal(_proposal("p4", ProposalKind.NEW_RECIPE, "rate", {"recipe": {}}))
    store.put_search_vector(
        SearchVector(definition_id="rate", version=2, model="m", doc_sha256="h", vector=(1.0,))
    )
    return store


def test_rename_rewrites_every_stored_copy(filled: Store) -> None:
    store = filled
    latest = store.rename_definition("rate", "rate_v", renamed_by="ann", renamed_at=WHEN)
    assert (latest.id, latest.version, latest.title) == ("rate_v", 2, "rate v2")
    assert store.get_definition("rate") is None and store.definition_history("rate") == []
    history = store.definition_history("rate_v")
    assert [(h.id, h.version, h.author, h.logic_changed) for h in history] == [
        ("rate_v", 1, "alice", True),
        ("rate_v", 2, "bob", False),
    ]
    assert store.get_definition("rate_v", 1).id == "rate_v"
    assert [d.id for d in store.list_definitions()] == ["other", "r1", "r2", "rate_v"]
    # Every version of every composed definition using it, pins kept; others untouched.
    assert store.get_definition("r1", 1).composition.uses["a"] == DefinitionRef(id="rate_v")
    r1 = store.get_definition("r1").composition
    assert r1.uses["a"] == DefinitionRef(id="rate_v", version=1)
    assert r1.uses["b"] == DefinitionRef(id="other")
    assert store.get_definition("r2").composition.uses["a"].id == "other"
    # Proposals: target + YAML, a composed definition's parts, a legacy recipe payload; a
    # rename's from/to kept; a recipe whose recipe id equals the old definition id
    # untouched.
    p1 = store.get_proposal("p1")
    assert p1.target_id == "rate_v" and "id: rate_v\n" in p1.payload["definition_yaml"]
    assert store.list_proposals(target_id="rate_v")[-1].id == "p1"
    assert "id: rate_v\n" in store.get_proposal("p2").payload["definition_yaml"]
    assert store.get_proposal("p2").target_id == "r3"
    assert store.get_proposal("p5").payload["recipe"]["uses"]["a"]["id"] == "rate_v"
    p3 = store.get_proposal("p3")
    assert p3.target_id == "rate_v" and p3.payload == {"from_id": "rate", "to_id": "x"}
    assert store.get_proposal("p4").target_id == "rate"
    assert store.get_proposal("p1").status is ProposalStatus.PENDING
    # Search vectors invalidated; the old id recorded.
    assert store.search_vectors("m") == []
    (former,) = store.former_ids()
    assert (former.former_id, former.definition_id, former.renamed_by) == ("rate", "rate_v", "ann")
    assert store.former_id("rate") == former and store.former_id("rate_v") is None


def test_refused_rename_changes_nothing(filled: Store) -> None:
    store = filled
    with pytest.raises(StoreError, match="'other' already exists"):
        store.rename_definition("rate", "other", renamed_by="ann")
    with pytest.raises(StoreError, match="'missing' does not exist"):
        store.rename_definition("missing", "x", renamed_by="ann")
    assert store.get_definition("rate").version == 2 and store.former_ids() == []
    assert store.get_definition("r1").composition.uses["a"].id == "rate"


def test_chain_points_every_former_id_at_the_current_one(filled: Store) -> None:
    store = filled
    store.rename_definition("rate", "b", renamed_by="ann")
    store.rename_definition("b", "c", renamed_by="ann")
    assert {f.former_id: f.definition_id for f in store.former_ids("c")} == {"rate": "c", "b": "c"}
    with pytest.raises(StoreError, match="'b' is a former id of definition 'c'"):
        store.rename_definition("other", "b", renamed_by="ann")
    store.rename_definition("c", "b", renamed_by="ann")  # its own former id: allowed
    assert [f.former_id for f in store.former_ids("b")] == ["rate", "c"]
