"""domain.search: the generated Markdown search document (ADR 0023)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from cteforge.domain.models import DefinitionState, JoinEdge, JoinKey
from cteforge.domain.search import SearchVector, document_sha256, search_document
from cteforge.files.definitions import load_definition

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"


@pytest.fixture
def definition():
    return load_definition(EXAMPLES / "definitions" / "booked_vs_collected.yml")


def edge(left: str, right: str, lkey: str, rkey: str, join_type: str = "inner") -> JoinEdge:
    return JoinEdge(
        id=f"{left}__{right}",
        left_table=left,
        right_table=right,
        keys=[JoinKey(left=lkey, right=rkey)],
        join_type=join_type,
    )


def test_document_has_every_field_in_order(definition) -> None:
    d = definition.model_copy(update={"join_keys": ["region"]})
    joins = [edge("deals", "invoices", "deal_id", "deal_id", "left")]
    doc = search_document(d, joins)
    assert doc.startswith(f"# {d.title}\n\nDefinition `booked_vs_collected`.\n\n{d.meaning}\n")
    headings = [line for line in doc.splitlines() if line.startswith("## ")]
    assert headings == [
        "## Use when",
        "## Don't use when",
        "## Keywords",
        "## Variables",
        "## Outputs",
        "## Grain",
        "## Tables",
        "## Joins",
    ]
    assert "booked revenue, collections, cash collected, bookings" in doc
    min_booked = next(line for line in doc.splitlines() if line.startswith("- `min_booked`"))
    assert min_booked.startswith("- `min_booked` (number, changeable): Leave out regions")
    assert "unit: thousand USD" in min_booked
    assert "- deals left join invoices on deals.deal_id = invoices.deal_id" in doc
    assert "- Join on output columns: region" in doc
    assert doc.endswith("\n")


def test_empty_sections_are_left_out(definition) -> None:
    bare = definition.model_copy(
        update={
            "meaning": "",
            "use_when": "",
            "dont_use_when": "  ",
            "keywords": [],
            "variables": [],
            "outputs": [],
            "grain": None,
            "tables": [],
            "join_keys": [],
        }
    )
    assert search_document(bare) == f"# {bare.title}\n\nDefinition `booked_vs_collected`.\n"
    # ADR 0024: former ids, sorted and de-duplicated, so an old name still finds it.
    renamed = search_document(bare, former_ids=["old_b", "old_a", "old_b", " "])
    assert renamed == (
        f"# {bare.title}\n\nDefinition `booked_vs_collected`. Formerly `old_a`, `old_b`.\n"
    )


def test_document_is_deterministic_and_ignores_state(definition) -> None:
    joins = [
        edge("deals", "invoices", "deal_id", "deal_id"),
        edge("accounts", "deals", "account_id", "account_id"),
    ]
    doc = search_document(definition, joins)
    assert search_document(definition, list(reversed(joins))) == doc
    shuffled = definition.model_copy(update={"tables": list(reversed(definition.tables))})
    assert search_document(shuffled, joins) == doc
    verified = definition.model_copy(
        update={
            "state": DefinitionState.VERIFIED,
            "verified_by": "ana",
            "verified_at": dt.date(2026, 1, 1),
            "version": 7,
        }
    )
    assert search_document(verified, joins) == doc
    lines = doc.splitlines()
    assert lines.index("- accounts inner join deals on accounts.account_id = deals.account_id") < (
        lines.index("- deals inner join invoices on deals.deal_id = invoices.deal_id")
    )


def test_hash_tracks_content(definition) -> None:
    doc = search_document(definition)
    assert document_sha256(doc) == document_sha256(search_document(definition))
    changed = definition.model_copy(update={"keywords": ["something else"]})
    assert document_sha256(search_document(changed)) != document_sha256(doc)
    assert len(document_sha256(doc)) == 64


def test_search_vector_dim() -> None:
    v = SearchVector(definition_id="a", version=1, model="m", doc_sha256="x", vector=(0.6, 0.8))
    assert v.dim == 2
    with pytest.raises(ValueError):
        SearchVector(definition_id="a", version=1, model="m", doc_sha256="x", vector=())
