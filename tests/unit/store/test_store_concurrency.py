"""Regression tests: thread safety of the in-memory store and upsert races."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import event

from cteforge.domain.errors import StaleVersionError, StoreError
from cteforge.domain.models import Definition, JoinEdge, JoinKey
from cteforge.store import Store

THREADS = 8


def _run_together(n: int, work: Callable[[int], str]) -> list[str]:
    """Start `n` threads behind a barrier; collect each result or exception class name."""
    barrier = threading.Barrier(n)
    results: list[str] = []
    guard = threading.Lock()

    def run(i: int) -> None:
        barrier.wait()
        try:
            outcome = work(i)
        except Exception as exc:  # noqa: BLE001 - every failure is a test datum
            outcome = type(exc).__name__
        with guard:
            results.append(outcome)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    return results


def test_memory_store_concurrent_writers_one_winner(
    store: Store, make_def: Callable[..., Definition]
) -> None:
    for round_no in range(5):
        id = f"d{round_no}"
        store.add_definition_version(make_def(id), author="a")

        def write(_i: int, id: str = id) -> str:
            store.add_definition_version(make_def(id, 2), author="a")
            return "ok"

        results = _run_together(THREADS, write)
        assert results.count("ok") == 1, results
        assert results.count(StaleVersionError.__name__) == THREADS - 1, results
        assert store.latest_version(id) == 2
        assert [h.version for h in store.definition_history(id)] == [1, 2]


def test_memory_store_concurrent_inserts_all_persist(
    store: Store, make_def: Callable[..., Definition]
) -> None:
    per_thread = 15

    def write(i: int) -> str:
        for j in range(per_thread):
            store.add_definition_version(make_def(f"t{i}_{j}"), author="a")
            assert store.get_definition(f"t{i}_{j}") is not None
        return "ok"

    assert _run_together(THREADS, write) == ["ok"] * THREADS
    assert len(store.list_definitions()) == THREADS * per_thread


@contextmanager
def _first_update_misses(store: Store, table: str) -> Iterator[list[str]]:
    """Make the next UPDATE of `table` match no rows, as if a concurrent INSERT committed
    after it ran (the READ COMMITTED race): the following INSERT then hits the key."""
    seen: list[str] = []

    def rewrite(
        _conn: Any, _cursor: Any, statement: str, params: Any, _ctx: Any, _many: bool
    ) -> tuple[str, Any]:
        if statement.startswith(f"UPDATE {table} ") and not seen:
            seen.append(statement)
            statement = f"{statement} AND 1 = 0"
        return statement, params

    event.listen(store._engine, "before_cursor_execute", rewrite, retval=True)
    try:
        yield seen
    finally:
        event.remove(store._engine, "before_cursor_execute", rewrite)


def test_set_setting_retries_when_insert_loses_race(store: Store) -> None:
    store.set_setting("mode", "open")
    with _first_update_misses(store, "settings") as seen:
        store.set_setting("mode", "strict")
    assert len(seen) == 1  # The race was actually injected.
    assert store.get_setting("mode") == "strict"


def test_upsert_join_retries_when_insert_loses_race(store: Store) -> None:
    edge = JoinEdge(
        id="deals_accounts",
        left_table="deals",
        right_table="accounts",
        keys=[JoinKey(left="account_id", right="id")],
    )
    store.upsert_join(edge)
    replaced = edge.model_copy(update={"right_table": "Customers"})
    with _first_update_misses(store, "join_edges") as seen:
        assert store.upsert_join(replaced) == replaced
    assert len(seen) == 1
    got = store.get_join("deals_accounts")
    assert got is not None and got.right_table == "Customers"
    assert len(store.list_joins()) == 1


def test_upsert_still_reports_persistent_conflict(store: Store) -> None:
    """The retry is bounded: a key that keeps conflicting surfaces as StoreError."""
    store.set_setting("mode", "open")
    calls: list[int] = []

    def always_miss(
        _conn: Any, _cursor: Any, statement: str, params: Any, _ctx: Any, _many: bool
    ) -> tuple[str, Any]:
        if statement.startswith("UPDATE settings "):
            calls.append(1)
            statement = f"{statement} AND 1 = 0"
        return statement, params

    event.listen(store._engine, "before_cursor_execute", always_miss, retval=True)
    try:
        try:
            store.set_setting("mode", "strict")
        except StoreError as exc:
            assert type(exc) is StoreError
        else:
            raise AssertionError("expected StoreError")
    finally:
        event.remove(store._engine, "before_cursor_execute", always_miss)
    assert len(calls) == 2
    assert store.get_setting("mode") == "open"
