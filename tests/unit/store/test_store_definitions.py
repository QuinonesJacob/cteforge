from __future__ import annotations

import datetime as dt
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from cteforge.domain.errors import StaleVersionError, StoreError
from cteforge.domain.models import Definition, DefinitionState
from cteforge.store import Store
from cteforge.store.store import Store as StoreClass

MakeDef = Callable[..., Definition]


def test_realistic_definition_round_trips(store: Store, realization_rate: Definition) -> None:
    stored = store.add_definition_version(realization_rate, author="jq")
    assert stored == realization_rate
    assert store.get_definition("realization_rate") == realization_rate
    assert store.get_definition("realization_rate", 1) == realization_rate
    assert store.latest_version("realization_rate") == 1


def test_missing_definition_returns_none(store: Store, make_def: MakeDef) -> None:
    assert store.get_definition("nope") is None
    assert store.latest_version("nope") is None
    assert store.definition_history("nope") == []
    store.add_definition_version(make_def("a"), author="jq")
    assert store.get_definition("a", 2) is None


def test_new_version_becomes_latest_and_old_stays_readable(store: Store, make_def: MakeDef) -> None:
    store.add_definition_version(make_def("a", 1), author="jq")
    store.add_definition_version(make_def("a", 2), author="ann", change_note="fix")
    assert store.latest_version("a") == 2
    assert store.get_definition("a").title == "a v2"
    assert store.get_definition("a", 1).title == "a v1"


@pytest.mark.parametrize("bad_version", [1, 3, 5])
def test_wrong_next_version_is_stale_and_leaves_no_trace(
    store: Store, make_def: MakeDef, bad_version: int
) -> None:
    store.add_definition_version(make_def("a", 1), author="jq")
    with pytest.raises(StaleVersionError):
        store.add_definition_version(make_def("a", bad_version), author="jq")
    assert store.latest_version("a") == 1
    assert [h.version for h in store.definition_history("a")] == [1]


def test_new_definition_must_start_at_version_one(store: Store, make_def: MakeDef) -> None:
    with pytest.raises(StaleVersionError):
        store.add_definition_version(make_def("a", 2), author="jq")
    assert store.latest_version("a") is None


def test_concurrent_revision_loses_on_pointer_update(
    store: Store, make_def: MakeDef, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two revisions of v1 race: the loser read latest=1 but v2 landed first."""
    store.add_definition_version(make_def("a", 1), author="jq")
    store.add_definition_version(make_def("a", 2), author="first")
    monkeypatch.setattr(StoreClass, "_latest", staticmethod(lambda *_: 1))
    with pytest.raises(StaleVersionError):
        store.add_definition_version(make_def("a", 2), author="second")
    monkeypatch.undo()
    history = store.definition_history("a")
    assert [(h.version, h.author) for h in history] == [(1, "jq"), (2, "first")]


def test_concurrent_creation_loses_on_unique_id(
    store: Store, make_def: MakeDef, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.add_definition_version(make_def("a", 1), author="first")
    monkeypatch.setattr(StoreClass, "_latest", staticmethod(lambda *_: None))
    with pytest.raises(StaleVersionError):
        store.add_definition_version(make_def("a", 1), author="second")
    monkeypatch.undo()
    assert [h.author for h in store.definition_history("a")] == ["first"]


def test_history_is_oldest_first_with_metadata(store: Store, make_def: MakeDef) -> None:
    before = dt.datetime.now(dt.UTC)
    store.add_definition_version(make_def("a", 1), author="jq", change_note="created")
    store.add_definition_version(
        make_def("a", 2), author="ann", change_note="keywords", logic_changed=False
    )
    store.add_definition_version(make_def("a", 3), author="bot")
    history = store.definition_history("a")
    assert [h.version for h in history] == [1, 2, 3]
    assert [h.author for h in history] == ["jq", "ann", "bot"]
    assert [h.logic_changed for h in history] == [True, False, True]
    assert history[0].change_note == "created"
    assert all(h.id == "a" and h.state is DefinitionState.DRAFT for h in history)
    for h in history:
        assert h.created_at.tzinfo is not None
        assert h.created_at.utcoffset() == dt.timedelta(0)
        assert before - dt.timedelta(seconds=1) <= h.created_at <= dt.datetime.now(dt.UTC)


def test_list_definitions_returns_latest_of_each_sorted(store: Store, make_def: MakeDef) -> None:
    store.add_definition_version(make_def("b", 1), author="jq")
    store.add_definition_version(make_def("a", 1), author="jq")
    store.add_definition_version(make_def("a", 2), author="jq")
    listed = store.list_definitions()
    assert [(d.id, d.version) for d in listed] == [("a", 2), ("b", 1)]


def test_list_definitions_filters_by_state_of_latest(store: Store, make_def: MakeDef) -> None:
    store.add_definition_version(make_def("a", 1, state=DefinitionState.VERIFIED), author="jq")
    store.add_definition_version(make_def("a", 2), author="jq")  # latest is draft
    store.add_definition_version(make_def("b", 1, state=DefinitionState.LLM_ANNOTATED), author="jq")
    assert store.list_definitions({DefinitionState.VERIFIED}) == []
    drafts = store.list_definitions({DefinitionState.DRAFT})
    assert [(d.id, d.version) for d in drafts] == [("a", 2)]
    usable = store.list_definitions({DefinitionState.DRAFT, DefinitionState.LLM_ANNOTATED})
    assert [d.id for d in usable] == ["a", "b"]
    assert store.list_definitions(set()) == []


def test_set_state_updates_yaml_index_and_history(store: Store, make_def: MakeDef) -> None:
    store.add_definition_version(make_def("a", 1), author="jq")
    day = dt.date(2026, 11, 16)
    result = store.set_definition_state(
        "a", 1, DefinitionState.VERIFIED, verified_by="ann", verified_at=day
    )
    assert (result.state, result.verified_by, result.verified_at) == (
        DefinitionState.VERIFIED,
        "ann",
        day,
    )
    assert store.get_definition("a") == result
    assert [d.id for d in store.list_definitions({DefinitionState.VERIFIED})] == ["a"]
    info = store.definition_history("a")[0]
    assert (info.state, info.verified_by, info.verified_at) == (
        DefinitionState.VERIFIED,
        "ann",
        day,
    )


def test_set_state_can_clear_verification(store: Store, make_def: MakeDef) -> None:
    store.add_definition_version(
        make_def(
            "a",
            1,
            state=DefinitionState.VERIFIED,
            verified_by="ann",
            verified_at=dt.date(2026, 1, 1),
        ),
        author="jq",
    )
    store.set_definition_state("a", 1, DefinitionState.DRAFT)
    got = store.get_definition("a")
    assert (got.state, got.verified_by, got.verified_at) == (DefinitionState.DRAFT, None, None)


def test_set_state_on_old_version_does_not_touch_latest_index(
    store: Store, make_def: MakeDef
) -> None:
    store.add_definition_version(make_def("a", 1), author="jq")
    store.add_definition_version(make_def("a", 2), author="jq")
    store.set_definition_state("a", 1, DefinitionState.VERIFIED, verified_by="ann")
    assert store.get_definition("a", 1).state is DefinitionState.VERIFIED
    assert store.get_definition("a").state is DefinitionState.DRAFT
    assert store.list_definitions({DefinitionState.VERIFIED}) == []


def test_set_state_on_missing_version_raises(store: Store, make_def: MakeDef) -> None:
    with pytest.raises(StoreError):
        store.set_definition_state("nope", 1, DefinitionState.VERIFIED)
    store.add_definition_version(make_def("a", 1), author="jq")
    with pytest.raises(StoreError):
        store.set_definition_state("a", 2, DefinitionState.VERIFIED)


def test_init_schema_is_idempotent_and_keeps_data(file_url: str, make_def: MakeDef) -> None:
    store = Store(file_url)
    store.init_schema()
    store.add_definition_version(make_def("a", 1), author="jq")
    store.set_setting("mode", "open")
    store.init_schema()
    reopened = Store(file_url)
    reopened.init_schema()
    assert reopened.latest_version("a") == 1
    assert reopened.get_setting("mode") == "open"


def test_memory_database_persists_across_calls() -> None:
    store = Store("sqlite://")
    store.init_schema()
    store.set_setting("k", "v")
    assert store.get_setting("k") == "v"


def test_newer_schema_version_is_refused(file_url: str, tmp_path: Path) -> None:
    Store(file_url).init_schema()
    with sqlite3.connect(tmp_path / "cteforge.db") as conn:
        conn.execute("UPDATE schema_meta SET value = '99' WHERE key = 'schema_version'")
    with pytest.raises(StoreError, match="newer"):
        Store(file_url).init_schema()


def test_uninitialized_database_raises_store_error() -> None:
    with pytest.raises(StoreError):
        Store("sqlite://").get_definition("a")


def test_bad_url_raises_without_leaking_password() -> None:
    with pytest.raises(StoreError) as info:
        Store("postgresql+nosuchdriver://user:s3cret@host/db")
    assert "s3cret" not in str(info.value)
