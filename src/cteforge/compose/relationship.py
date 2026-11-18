"""Relationship check for a join edge (ADR 0026). Pure: builds SQL, reads counts.

A claimed relationship (`one_to_many`, ...) is checked against the warehouse by counting,
on each side, the rows whose key columns are all set and the distinct key tuples:

    SELECT COUNT(*) AS row_count,
           (SELECT COUNT(*) FROM (SELECT DISTINCT k1, k2 FROM t WHERE k1 IS NOT NULL AND
             k2 IS NOT NULL) AS d) AS distinct_keys
    FROM t WHERE k1 IS NOT NULL AND k2 IS NOT NULL

Equal counts → that side is unique ("one"). The SQL is built only from sqlglot nodes:
table and column names become identifiers (`exp.to_identifier` quotes anything that is
not a plain identifier), never text spliced into SQL. No values are involved.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlglot import exp

from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import RenderError
from cteforge.parsing.analyze import generate

ONE, MANY = "one", "many"


def _table(name: str) -> exp.Table:
    parts = [p.strip() for p in name.split(".")]
    if not 1 <= len(parts) <= 3 or any(not p for p in parts):
        raise RenderError(f"'{name}' is not a table name (name, schema.name or db.schema.name)")
    idents = [exp.to_identifier(p) for p in parts]
    while len(idents) < 3:
        idents.insert(0, None)  # type: ignore[arg-type]
    catalog, db, table = idents
    return exp.Table(this=table, db=db, catalog=catalog)


def _not_null(keys: Sequence[str]) -> exp.Expression:
    conditions: list[exp.Expression] = [
        exp.Not(this=exp.Is(this=exp.column(exp.to_identifier(k)), expression=exp.Null()))
        for k in keys
    ]
    return exp.and_(*conditions)


def relationship_query(table: str, keys: Sequence[str], dialect: Dialect) -> str:
    """The counting query for one side (see the module docstring), in `dialect`."""
    keys = [k.strip() for k in keys]
    if not keys or any(not k or "." in k for k in keys):
        raise RenderError("Each join key is one column name")
    where = _not_null(keys)
    distinct = (
        exp.select(*[exp.column(exp.to_identifier(k)) for k in keys])
        .distinct()
        .from_(_table(table))
        .where(where.copy())
    )
    distinct_count = exp.select(exp.Count(this=exp.Star())).from_(
        exp.Subquery(this=distinct, alias=exp.TableAlias(this=exp.to_identifier("d")))
    )
    query = (
        exp.select(
            exp.alias_(exp.Count(this=exp.Star()), "row_count"),
            exp.alias_(exp.Subquery(this=distinct_count), "distinct_keys"),
        )
        .from_(_table(table))
        .where(where)
    )
    return generate(query, dialect)


def side(rows: int, distinct_keys: int) -> str:
    """ "one" when every key appears once, else "many"."""
    return ONE if rows == distinct_keys else MANY


def relationship_of(left: str, right: str) -> str:
    """("one", "many") → "one_to_many", etc."""
    return f"{left}_to_{right}"


def claimed_sides(relationship: str) -> tuple[str, str]:
    """ "many_to_one" → ("many", "one")."""
    left, _, right = relationship.partition("_to_")
    if left not in (ONE, MANY) or right not in (ONE, MANY):
        raise RenderError(f"Unknown relationship '{relationship}'")
    return left, right


def consistent(claimed: str, observed: str) -> bool:
    """A claim fits the data when every side it calls "one" is unique in the data (data
    may be unique where the claim allows "many": a subset, not a contradiction)."""
    return all(
        c == MANY or o == ONE
        for c, o in zip(claimed_sides(claimed), claimed_sides(observed), strict=True)
    )
