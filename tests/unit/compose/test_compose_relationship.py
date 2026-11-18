"""compose.relationship (ADR 0026): the counting SQL is built from identifiers only."""

from __future__ import annotations

import duckdb
import pytest

from cteforge.compose.relationship import (
    claimed_sides,
    consistent,
    relationship_of,
    relationship_query,
    side,
)
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import RenderError
from cteforge.parsing.analyze import parse


def test_query_counts_rows_and_distinct_keys() -> None:
    con = duckdb.connect()
    con.execute("CREATE TABLE t (a INT, b INT)")
    con.execute("INSERT INTO t VALUES (1, 1), (1, 2), (1, 2), (NULL, 3), (2, NULL)")
    rows, distinct = con.execute(relationship_query("t", ["a"], Dialect.DUCKDB)).fetchone()
    assert (rows, distinct) == (4, 2)  # NULL keys are left out
    rows, distinct = con.execute(relationship_query("t", ["a", "b"], Dialect.DUCKDB)).fetchone()
    assert (rows, distinct) == (3, 2)


@pytest.mark.parametrize("dialect", list(Dialect))
def test_query_parses_in_every_dialect(dialect: Dialect) -> None:
    sql = relationship_query("analytics.deals", ["account_id"], dialect)
    assert parse(sql, dialect) is not None


def test_hostile_names_stay_identifiers() -> None:
    sql = relationship_query('t"; DROP TABLE x; --', ["k) OR (1=1"], Dialect.POSTGRES)
    tree = parse(sql, Dialect.POSTGRES)  # one statement: nothing was spliced in
    assert tree.key == "select" and "DROP" not in [n.key.upper() for n in tree.walk()]
    with pytest.raises(RenderError):
        relationship_query("a.b.c.d", ["k"], Dialect.DUCKDB)
    with pytest.raises(RenderError):
        relationship_query("t", ["x.k"], Dialect.DUCKDB)
    with pytest.raises(RenderError):
        relationship_query("t", [], Dialect.DUCKDB)


def test_claims_against_observations() -> None:
    assert side(5, 5) == "one" and side(5, 3) == "many"
    assert relationship_of("one", "many") == "one_to_many"
    assert claimed_sides("many_to_one") == ("many", "one")
    assert consistent("one_to_many", "one_to_one")  # data may be stricter than the claim
    assert consistent("many_to_many", "one_to_one")
    assert not consistent("one_to_one", "one_to_many")
    assert not consistent("one_to_many", "many_to_many")
    with pytest.raises(RenderError):
        claimed_sides("several")
