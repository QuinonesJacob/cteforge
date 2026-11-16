"""Analyze a trusted query: CTEs, tables, outputs, and the literals that could become
variables — with the column each literal filters, resolved through wrappers and CTEs.

Pure. The schema (table → column → type string) is fetched by services/ and passed in.

Literal IDs are `<scope>.L<n>`: scope is the CTE name (or "main"), n counts filter
literals in depth-first order within that scope. IDs are stable for unchanged SQL and are
what annotations refer to. `find_literals` is shared with templating so both sides agree.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import sqlglot
from pydantic import BaseModel, ConfigDict
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import SqlParseError
from cteforge.domain.models import LiteralForm
from cteforge.domain.variables import RangeShape

Schema = dict[str, dict[str, str]]  # table -> column -> warehouse type string
MAIN_SCOPE = "main"

_COMPARISONS: dict[type[exp.Expression], str] = {
    exp.EQ: "=",
    exp.NEQ: "<>",
    exp.GT: ">",
    exp.GTE: ">=",
    exp.LT: "<",
    exp.LTE: "<=",
    exp.Like: "LIKE",
    exp.ILike: "ILIKE",
}
_LOW_OPS = {">", ">="}
_HIGH_OPS = {"<", "<="}


# ── Result models ───────────────────────────────────────────────────────────────────


class ColumnRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    table: str | None  # Base table, or CTE name if the column is derived inside a CTE.
    column: str
    type: str | None = None  # Warehouse type; None if unknown or derived.
    derived: bool = False  # True when lineage stops at a computed CTE column.


class LiteralInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    scope: str
    value: str  # Literal text, e.g. "2026-01-01", "EMEA", "100".
    is_string: bool
    form: LiteralForm
    cast_type: str | None = None  # Source-dialect SQL type when form is TYPED.
    operator: str  # "=", ">=", "IN", "BETWEEN_LOW", ...
    column_sql: str | None  # The filtered expression as written, e.g. "LOWER(a.region)".
    column: ColumnRef | None
    wrapped: bool  # True if the column is inside a function/expression.
    in_list: str | None = None  # Shared by all literals of one IN (...) list.


class RangeCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    scope: str
    column_sql: str
    start: str  # literal id
    end: str  # literal id
    shape: RangeShape


class CteInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    reads: list[str]  # Tables and CTEs it selects from.
    outputs: list[str]


class QueryAnalysis(BaseModel):
    model_config = ConfigDict(frozen=True)

    dialect: Dialect
    ctes: list[CteInfo]
    tables: list[str]  # Base tables (not CTEs).
    outputs: list[str]
    literals: list[LiteralInfo]
    ranges: list[RangeCandidate]

    def literal(self, literal_id: str) -> LiteralInfo | None:
        return next((lit for lit in self.literals if lit.id == literal_id), None)


# ── Parsing helpers (shared with templating) ────────────────────────────────────────


def parse(sql: str, dialect: Dialect) -> exp.Expression:
    """Parse exactly one statement or raise SqlParseError."""
    try:
        statements = [s for s in sqlglot.parse(sql, read=str(dialect)) if s is not None]
    except (ParseError, TokenError) as exc:
        raise SqlParseError(f"Could not parse SQL as {dialect}: {exc}") from exc
    if len(statements) != 1:
        raise SqlParseError(f"Expected exactly one SQL statement, got {len(statements)}")
    return statements[0]


@dataclass(frozen=True)
class FoundLiteral:
    """A filter literal located in a parsed tree (internal; not serialized)."""

    id: str
    scope: str
    node: exp.Expression  # The node to replace: Cast for typed literals, else Literal.
    literal: exp.Literal | exp.Boolean
    predicate: exp.Expression  # The comparison / IN / BETWEEN it belongs to.
    operator: str
    other_side: exp.Expression | None  # The filtered expression.


def _top_with(tree: exp.Expression) -> exp.With | None:
    with_ = tree.args.get("with") or tree.args.get("with_")
    return with_ if isinstance(with_, exp.With) else None


def scopes(tree: exp.Expression) -> list[tuple[str, exp.Expression]]:
    """(scope name, subtree) for each top-level CTE in order, then the main query."""
    result: list[tuple[str, exp.Expression]] = []
    with_ = _top_with(tree)
    if with_ is not None:
        result.extend((cte.alias_or_name, cte.this) for cte in with_.expressions)
    result.append((MAIN_SCOPE, tree))
    return result


def _walk(scope_root: exp.Expression, skip: exp.Expression | None) -> Iterator[exp.Expression]:
    stack = [scope_root]
    while stack:
        node = stack.pop()
        if skip is not None and node is skip:
            continue
        yield node
        children = list(node.iter_expressions())
        stack.extend(reversed(children))


def _classify(
    node: exp.Expression,
) -> tuple[exp.Expression, str, exp.Expression | None] | None:
    """If `node` is a filter value, return (predicate, operator, other side)."""
    parent = node.parent
    while isinstance(parent, exp.Paren):
        node, parent = parent, parent.parent
    if isinstance(parent, exp.In) and node in parent.expressions:
        return parent, "IN", parent.this
    if isinstance(parent, exp.Between):
        if node is parent.args.get("low"):
            return parent, "BETWEEN_LOW", parent.this
        if node is parent.args.get("high"):
            return parent, "BETWEEN_HIGH", parent.this
        return None
    op = _COMPARISONS.get(type(parent))
    if op is None:
        return None
    other = parent.left if node is parent.right else parent.right
    if other is None or isinstance(other, exp.Literal | exp.Cast | exp.Boolean):
        return None
    if node is parent.left:  # literal on the left: 5 < x  →  x > 5
        op = {">": "<", ">=": "<=", "<": ">", "<=": ">="}.get(op, op)
    return parent, op, other


def find_literals(tree: exp.Expression) -> list[FoundLiteral]:
    """All filter literals in the tree with stable IDs (see module docstring)."""
    found: list[FoundLiteral] = []
    with_ = _top_with(tree)
    for scope_name, root in scopes(tree):
        skip = with_ if scope_name == MAIN_SCOPE else None
        ordinal = 0
        for node in _walk(root, skip):
            if not isinstance(node, exp.Literal | exp.Boolean):
                continue
            target: exp.Expression = node
            if isinstance(node.parent, exp.Cast) and node.parent.this is node:
                target = node.parent
            elif isinstance(node.parent, exp.Neg):
                target = node.parent
            classified = _classify(target)
            if classified is None:
                continue
            predicate, operator, other = classified
            ordinal += 1
            found.append(
                FoundLiteral(
                    id=f"{scope_name}.L{ordinal}",
                    scope=scope_name,
                    node=target,
                    literal=node,
                    predicate=predicate,
                    operator=operator,
                    other_side=other,
                )
            )
    return found


# ── Column lineage ──────────────────────────────────────────────────────────────────


def _sources(select: exp.Select) -> dict[str, str]:
    """alias-or-name → table/CTE name for tables directly in this SELECT's FROM/JOINs."""
    return {
        t.alias_or_name: t.name
        for t in select.find_all(exp.Table)
        if t.find_ancestor(exp.Select) is select
    }


def _cte_selects(tree: exp.Expression) -> dict[str, exp.Expression]:
    return {name: root for name, root in scopes(tree) if name != MAIN_SCOPE}


def _resolve(
    column: exp.Column,
    ctes: dict[str, exp.Expression],
    schema: Schema,
    depth: int = 0,
) -> ColumnRef:
    """Trace a column to its base table (through CTEs) and look up its type."""
    name = column.name
    select = column.find_ancestor(exp.Select)
    if select is None:
        return ColumnRef(table=None, column=name)
    sources = _sources(select)
    if column.table:
        source = sources.get(column.table)
    elif len(sources) == 1:
        source = next(iter(sources.values()))
    else:  # Unqualified with several sources: pick the one that has this column.
        source = next(
            (
                s
                for s in sources.values()
                if name in schema.get(s, {}) or (s in ctes and name in ctes[s].named_selects)
            ),
            None,
        )
    if source is None:
        return ColumnRef(table=None, column=name)
    return _resolve_in_source(source, name, ctes, schema, depth)


def _resolve_in_source(
    source: str,
    name: str,
    ctes: dict[str, exp.Expression],
    schema: Schema,
    depth: int,
) -> ColumnRef:
    if source not in ctes:
        return ColumnRef(table=source, column=name, type=schema.get(source, {}).get(name))
    root = ctes[source]
    if depth > 20 or not isinstance(root, exp.Select):
        return ColumnRef(table=source, column=name, derived=True)
    for projection in root.selects:
        if isinstance(projection, exp.Star):
            inner_sources = list(_sources(root).values())
            if len(inner_sources) == 1:
                return _resolve_in_source(inner_sources[0], name, ctes, schema, depth + 1)
        elif projection.alias_or_name == name:
            inner = projection.unalias()
            if isinstance(inner, exp.Column):
                return _resolve(inner, ctes, schema, depth + 1)
            return ColumnRef(table=source, column=name, derived=True)
    return ColumnRef(table=source, column=name, derived=True)


# ── Public API ──────────────────────────────────────────────────────────────────────


def referenced_tables(sql: str, dialect: Dialect) -> list[str]:
    """Base tables a query reads (CTE names excluded). Used to fetch the schema first."""
    tree = parse(sql, dialect)
    cte_names = set(_cte_selects(tree))
    return sorted({t.name for t in tree.find_all(exp.Table) if t.name not in cte_names})


def analyze(sql: str, dialect: Dialect, schema: Schema | None = None) -> QueryAnalysis:
    schema = {k.lower(): {c.lower(): t for c, t in v.items()} for k, v in (schema or {}).items()}
    tree = parse(sql, dialect)
    ctes = _cte_selects(tree)
    cte_names = set(ctes)

    cte_infos = [
        CteInfo(
            name=name,
            reads=sorted({t.name for t in root.find_all(exp.Table)}),
            outputs=list(root.named_selects),
        )
        for name, root in ctes.items()
    ]

    literals: list[LiteralInfo] = []
    in_groups: dict[int, str] = {}
    for found in find_literals(tree):
        other = found.other_side
        column_node = (
            other
            if isinstance(other, exp.Column)
            else (other.find(exp.Column) if other is not None else None)
        )
        in_list = None
        if found.operator == "IN":
            key = id(found.predicate)
            in_groups.setdefault(key, f"{found.scope}.IN{len(in_groups) + 1}")
            in_list = in_groups[key]
        cast_type = None
        form = LiteralForm.BARE
        if isinstance(found.node, exp.Cast):
            form = LiteralForm.TYPED
            cast_type = found.node.to.sql(dialect=str(dialect))
        value = (
            found.literal.name
            if isinstance(found.literal, exp.Literal)
            else ("TRUE" if found.literal.this else "FALSE")
        )
        if isinstance(found.node, exp.Neg):
            value = f"-{value}"
        literals.append(
            LiteralInfo(
                id=found.id,
                scope=found.scope,
                value=value,
                is_string=isinstance(found.literal, exp.Literal) and found.literal.is_string,
                form=form,
                cast_type=cast_type,
                operator=found.operator,
                column_sql=other.sql(dialect=str(dialect)) if other is not None else None,
                column=_resolve(column_node, ctes, schema) if column_node else None,
                wrapped=other is not None and not isinstance(other, exp.Column),
                in_list=in_list,
            )
        )

    return QueryAnalysis(
        dialect=dialect,
        ctes=cte_infos,
        tables=sorted({t.name for t in tree.find_all(exp.Table) if t.name not in cte_names}),
        outputs=list(tree.named_selects),
        literals=literals,
        ranges=_ranges(literals, find_literals(tree)),
    )


def _ranges(literals: list[LiteralInfo], found: list[FoundLiteral]) -> list[RangeCandidate]:
    """Pair low/high bounds on the same column in the same scope into range candidates."""
    candidates: list[RangeCandidate] = []
    by_id = {lit.id: lit for lit in literals}
    # BETWEEN: low and high share a predicate.
    lows = {id(f.predicate): f for f in found if f.operator == "BETWEEN_LOW"}
    for f in found:
        if f.operator == "BETWEEN_HIGH" and id(f.predicate) in lows:
            low = lows[id(f.predicate)]
            candidates.append(
                RangeCandidate(
                    scope=f.scope,
                    column_sql=by_id[f.id].column_sql or "",
                    start=low.id,
                    end=f.id,
                    shape=RangeShape.INCLUSIVE,
                )
            )
    # col >= a AND col < b  (or <=)
    groups: dict[tuple[str, str], list[LiteralInfo]] = {}
    for lit in literals:
        if lit.column_sql and lit.operator in _LOW_OPS | _HIGH_OPS:
            groups.setdefault((lit.scope, lit.column_sql), []).append(lit)
    for (scope, column_sql), lits in groups.items():
        low = [x for x in lits if x.operator in _LOW_OPS]
        high = [x for x in lits if x.operator in _HIGH_OPS]
        if len(low) == 1 and len(high) == 1:
            shape = RangeShape.HALF_OPEN if high[0].operator == "<" else RangeShape.INCLUSIVE
            candidates.append(
                RangeCandidate(
                    scope=scope,
                    column_sql=column_sql,
                    start=low[0].id,
                    end=high[0].id,
                    shape=shape,
                )
            )
    return candidates
