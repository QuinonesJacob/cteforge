"""Store: meaning-search vectors (ADR 0023) — derived data, one per version per model."""

from __future__ import annotations

import struct

import pytest
from sqlalchemy import update

from cteforge.domain.errors import StoreError
from cteforge.domain.search import SearchVector
from cteforge.store import Store
from cteforge.store import tables as t


def vec(id: str, version: int = 1, model: str = "m1", values=(0.6, 0.8), sha="s1") -> SearchVector:
    return SearchVector(
        definition_id=id, version=version, model=model, doc_sha256=sha, vector=tuple(values)
    )


def test_put_and_read_back_float32(store: Store) -> None:
    store.put_search_vector(vec("b", values=(0.1, 0.2, 0.3)))
    store.put_search_vector(vec("a", version=2))
    store.put_search_vector(vec("a", version=1))
    got = store.search_vectors("m1")
    assert [(v.definition_id, v.version) for v in got] == [("a", 1), ("a", 2), ("b", 1)]
    b = got[2]
    # Stored as float32: equal after the same rounding, not bit-equal to the doubles.
    assert b.vector == struct.unpack("<3f", struct.pack("<3f", 0.1, 0.2, 0.3))
    assert b.dim == 3 and b.doc_sha256 == "s1" and b.model == "m1"
    assert store.search_vectors("other") == []


def test_put_replaces_same_version_and_model(store: Store) -> None:
    store.put_search_vector(vec("a", sha="old", values=(1.0, 0.0)))
    store.put_search_vector(vec("a", sha="new", values=(0.0, 1.0)))
    store.put_search_vector(vec("a", model="m2"))
    (only,) = store.search_vectors("m1")
    assert only.doc_sha256 == "new" and only.vector == (0.0, 1.0)
    assert store.search_vector_models() == ["m1", "m2"]


def test_delete_by_model_or_all(store: Store) -> None:
    store.put_search_vector(vec("a"))
    store.put_search_vector(vec("b"))
    store.put_search_vector(vec("a", model="m2"))
    assert store.delete_search_vectors("m1") == 2
    assert store.search_vector_models() == ["m2"]
    assert store.delete_search_vectors() == 1
    assert store.search_vector_models() == []
    assert store.delete_search_vectors() == 0


def test_corrupt_blob_is_a_store_error(store: Store) -> None:
    store.put_search_vector(vec("a"))
    with store._tx() as conn:  # Simulate a damaged row.
        conn.execute(update(t.search_vectors).values(dim=5))
    with pytest.raises(StoreError):
        store.search_vectors("m1")


def test_table_is_created_on_an_existing_database(file_url: str) -> None:
    first = Store(file_url)
    first.init_schema()
    with first._tx() as conn:
        t.search_vectors.drop(conn)  # A database from before meaning search.
    first.close()
    again = Store(file_url)
    again.init_schema()
    again.put_search_vector(vec("a"))
    assert len(again.search_vectors("m1")) == 1
    again.close()
