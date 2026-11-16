"""Analyze a trusted query: CTEs, tables, joins, outputs, and the literals that could become
variables — with the column each literal filters (through wrappers and CTEs), structural
facts about where each literal sits, and suggested range pairs (ADR 0014, 0015).

Pure. The schema (table → column → type string) is fetched by services/ and passed in.

Scopes: each top-level CTE (named as written), then the outer query. The outer query is
`main`; if a CTE already has that name (case-insensitive) it is the first free name of
`main_2`, `main_3`, ... so IDs never collide.

Literal IDs are `<scope>.L<n>`: n counts filter literals in depth-first, pre-order,
argument order within the scope. A filter literal is one whose path up to a comparison /
IN / BETWEEN / LIKE goes only through value-side wrappers (functions, arithmetic,
concatenation, parentheses) and list containers (ANY/ALL arrays, IN UNNEST, row values),
and whose operand holds no column, while the other side does. Format and date-part unit
arguments of functions are never literals. IDs are stable for unchanged SQL and are what
annotations refer to. `find_literals` is shared with templating so both sides agree.

CTE names are positional: a CTE's own body and earlier CTEs read the base table of that
name (WITH RECURSIVE: every CTE is visible everywhere).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

import sqlglot
from pydantic import BaseModel, ConfigDict
from sqlglot import exp
from sqlglot.errors import ParseError, SqlglotError, TokenError
from sqlglot.optimizer.annotate_types import annotate_types

from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import SqlParseError
from cteforge.domain.models import LiteralForm
from cteforge.domain.variables import RangeShape
from cteforge.parsing.types import data_type

# Table key → column → warehouse type. Keys: "table", "schema.table" or "db.schema.table",
# any case (matching rules: SchemaIndex).
Schema = dict[str, dict[str, str]]
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
    exp.NullSafeEQ: "IS NOT DISTINCT FROM",
    exp.NullSafeNEQ: "IS DISTINCT FROM",
    exp.SimilarTo: "SIMILAR TO",
}
_PREDICATES = (*_COMPARISONS, exp.In, exp.Between)
_FLIP = {">": "<", ">=": "<=", "<": ">", "<=": ">="}
_LOW_OPS = {">", ">="}
_HIGH_OPS = {"<", "<="}
_SHAPES = {
    (">=", "<"): RangeShape.HALF_OPEN,
    (">=", "<="): RangeShape.INCLUSIVE,
    (">", "<"): RangeShape.EXCLUSIVE,
    (">", "<="): RangeShape.START_EXCLUSIVE,
}
# Value-side wrappers a literal may sit in (minus boolean logic and predicates).
_WRAPPERS = (exp.Func, exp.Binary, exp.Paren, exp.Neg, exp.Interval)
_NOT_WRAPPERS = (exp.Predicate, exp.Connector, exp.Not, exp.Query)
# Arguments that are never values: format strings, date-part units, parser-injected defaults.
_NON_VALUE_ARGS = {"format", "default_year", "unit"}
# Functions sqlglot leaves unparsed (Anonymous) whose first argument is a date-part unit.
_UNIT_FIRST_FUNCS = {
    "DATE_PART",
    "DATEPART",
    "DATE_TRUNC",
    "DATETRUNC",
    "DATEADD",
    "DATEDIFF",
    "TIMESTAMPADD",
    "TIMESTAMPDIFF",
}
# Quantifiers some dialects parse as plain functions (`x <> ALL(ARRAY[...])`).
_QUANTIFIER_FUNCS = {"ANY", "SOME", "ALL"}
# Date-parsing functions whose `format` argument describes their `this` argument.
_PARSE_FUNCS = (
    exp.StrToTime,
    exp.StrToDate,
    exp.TsOrDsToDate,
    exp.TsOrDsToTimestamp,
    exp.TsOrDsToDatetime,
)
# Nodes that make an expression depend on rows (a "column side").
_REFERENCES = (exp.Column, exp.AggFunc, exp.Window, exp.Subquery, exp.Select)
# Column-side wrappers whose result keeps the column's type (fallback when annotation fails).
_TYPE_PRESERVING = (
    exp.Paren,
    exp.Neg,
    exp.Abs,
    exp.Coalesce,
    exp.Round,
    exp.Floor,
    exp.Ceil,
    exp.Trim,
    exp.Upper,
    exp.Lower,
    exp.DateTrunc,
    exp.TimestampTrunc,
)
_ARITHMETIC = (exp.Add, exp.Sub, exp.Mul, exp.Div)
_MAX_DEPTH = 64  # Lineage hops (CTE layers); types are memoized, so cost stays linear.


# ── Result models ───────────────────────────────────────────────────────────────────


class ColumnRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    # Base table, qualified as written (unquoted parts lower-cased); or the CTE / derived
    # table name if the column is computed there.
    table: str | None
    column: str
    type: str | None = None  # Warehouse type; None if unknown or derived.
    derived: bool = False  # True when lineage stops at a computed CTE column.


class LiteralContext(StrEnum):
    """Structural facts about where a literal sits (ADR 0015). Facts, not meaning."""

    NEGATED = "negated"  # Under a NOT (incl. NOT BETWEEN / NOT IN / NOT LIKE).
    OR_BRANCH = "or_branch"  # Inside one branch of an OR.
    CASE_CONDITION = "case_condition"  # In a CASE WHEN / IF / FILTER condition.
    JOIN_ON = "join_on"  # In an inner join's ON clause (filters rows like WHERE).
    OUTER_JOIN_ON = "outer_join_on"  # In an outer join's ON: filters attached rows only.
    HAVING = "having"  # Filters groups, after aggregation.
    SUBQUERY = "subquery"  # Inside a subquery (IN (SELECT ...), EXISTS, scalar, derived).
    VALUE_WRAPPED = "value_wrapped"  # Inside a function/arithmetic/concat on the value side.


class LiteralInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    scope: str
    value: str  # Literal text, e.g. "2026-01-01", "EMEA", "100".
    is_string: bool
    form: LiteralForm
    cast_type: str | None = None  # Source-dialect SQL type when form is TYPED.
    operator: str  # "=", ">=", "IN", "BETWEEN_LOW", "IS DISTINCT FROM", "= ANY", "ILIKE ANY",
    #               "<> ALL", "IN UNNEST", "ROW =" / "ROW IN" (element of a row value), ...
    column_sql: str | None  # The filtered expression as written, e.g. "LOWER(a.region)".
    column: ColumnRef | None
    wrapped: bool  # True if the column is inside a function/expression.
    in_list: str | None = None  # Shared by all literals of one IN (...) list.
    # ── ADR 0014/0015 (M1.1) ──
    context: list[LiteralContext] = []
    wrapper_sql: str | None = None  # Value-side wrapper, literal shown as "?", e.g.
    #                                  "STRPTIME(?, '%m-%d-%Y')", "? * 1e12", "'%' || ? || '%'".
    suggested_format: str | None = None  # strftime pattern from an adjacent format argument.
    compared_type: str | None = None  # Type of the column-side expression (the column's type
    #                                    when unwrapped; the expression's type when wrapped).


class RangeCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    scope: str
    column_sql: str
    start: str  # literal id
    end: str  # literal id
    shape: RangeShape


class JoinInfo(BaseModel):
    """A join found in the SQL, for mining the join map (DESIGN §3)."""

    model_config = ConfigDict(frozen=True)

    scope: str
    left_table: str  # Base table (resolved through aliases), or CTE name.
    right_table: str
    join_type: str  # inner, left, right, full, cross, semi, anti
    keys: list[tuple[str, str]]  # (left_column, right_column) equality pairs in ON.
    on_sql: str  # Full ON condition as written (may contain non-key filters).


class CteInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    reads: list[str]  # Tables and CTEs it selects from.
    outputs: list[str]


class QueryAnalysis(BaseModel):
    model_config = ConfigDict(frozen=True)

    dialect: Dialect
    ctes: list[CteInfo]
    tables: list[str]  # Base tables (not CTEs), qualified as written.
    outputs: list[str]
    literals: list[LiteralInfo]
    ranges: list[RangeCandidate]
    joins: list[JoinInfo] = []

    def literal(self, literal_id: str) -> LiteralInfo | None:
        return next((lit for lit in self.literals if lit.id == literal_id), None)


# ── Parsing and scopes (shared with templating) ─────────────────────────────────────


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
class Scope:
    name: str
    root: exp.Expression
    is_main: bool


def _top_with(tree: exp.Expression) -> exp.With | None:
    with_ = tree.args.get("with_") or tree.args.get("with")
    return with_ if isinstance(with_, exp.With) else None


def main_scope_name(cte_names: list[str]) -> str:
    """`main`, or `main_<n>` (first free n ≥ 2) when a CTE already has that name."""
    taken = {name.lower() for name in cte_names}
    name, n = MAIN_SCOPE, 1
    while name in taken:
        n += 1
        name = f"{MAIN_SCOPE}_{n}"
    return name


def scopes(tree: exp.Expression) -> list[Scope]:
    """One scope per top-level CTE in order, then the outer query."""
    with_ = _top_with(tree)
    ctes = [Scope(c.alias_or_name, c.this, False) for c in (with_.expressions if with_ else [])]
    return [*ctes, Scope(main_scope_name([s.name for s in ctes]), tree, True)]


def _walk(scope: Scope, tree: exp.Expression) -> Iterator[exp.Expression]:
    """Depth-first, pre-order, children in argument order. The outer query skips WITH."""
    skip = _top_with(tree) if scope.is_main else None
    stack = [scope.root]
    while stack:
        node = stack.pop()
        if skip is not None and node is skip:
            continue
        yield node
        stack.extend(reversed(list(node.iter_expressions())))


def _ident_key(identifier: exp.Expression | None) -> str:
    """Lookup key for an identifier: lower-cased unless quoted."""
    if identifier is None:
        return ""
    if isinstance(identifier, exp.Identifier) and identifier.quoted:
        return identifier.name
    return identifier.name.lower()


# ── Literal location ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FoundLiteral:
    """A filter literal located in a parsed tree (internal; not serialized)."""

    id: str
    scope: str
    node: exp.Expression  # The node to replace: Cast for typed, Neg for negative, else Literal.
    literal: exp.Literal | exp.Boolean
    predicate: exp.Expression  # The comparison / IN / BETWEEN / LIKE it belongs to.
    operator: str
    other_side: exp.Expression | None  # The filtered (column-side) expression.
    value_root: exp.Expression  # Predicate operand holding the literal (`node` if unwrapped).
    context: tuple[LiteralContext, ...]
    format_literal: exp.Literal | None  # Format argument of a date-parsing function around it.

    @property
    def value_wrapped(self) -> bool:
        return LiteralContext.VALUE_WRAPPED in self.context


@dataclass(frozen=True)
class _Located:
    node: exp.Expression
    value_root: exp.Expression
    predicate: exp.Expression
    operator: str
    other: exp.Expression


def _target(literal: exp.Expression) -> exp.Expression:
    parent = literal.parent
    if isinstance(parent, exp.Cast) and parent.this is literal:
        return parent
    if isinstance(parent, exp.Neg):
        return parent
    return literal


def _is_wrapper(node: exp.Expression) -> bool:
    return isinstance(node, _WRAPPERS) and not isinstance(node, _NOT_WRAPPERS)


def _has_reference(node: exp.Expression) -> bool:
    return node.find(*_REFERENCES) is not None


def _is_unit_arg(parent: exp.Expression, node: exp.Expression) -> bool:
    """`'month'` in an unparsed `DATE_PART('month', ...)`: a date-part unit, not a value."""
    return (
        isinstance(parent, exp.Anonymous)
        and parent.name.upper() in _UNIT_FIRST_FUNCS
        and node.arg_key == "expressions"
        and node.index == 0
    )


def _climbable(parent: exp.Expression, node: exp.Expression) -> bool:
    if node.arg_key in _NON_VALUE_ARGS or _is_unit_arg(parent, node):
        return False
    return _is_wrapper(parent) or isinstance(parent, exp.Tuple | exp.Any | exp.All)


def _locate(literal: exp.Expression) -> _Located | None:
    """Climb from a literal through value-side wrappers to its predicate, if any."""
    target = node = _target(literal)
    path = [node]
    while not isinstance(node.parent, _PREDICATES):
        parent = node.parent
        if parent is None or not _climbable(parent, node):
            return None
        node = parent
        path.append(node)
    predicate = node.parent
    operand = _classify(predicate, path[::-1])
    if operand is None:
        return None
    return _Located(target, operand.value_root, predicate, operand.operator, operand.other)


@dataclass(frozen=True)
class _Operand:
    operator: str
    value_root: exp.Expression  # Value-side expression holding the literal (a list element).
    other: exp.Expression | None  # The column side it is compared to.


def _quantifier(node: exp.Expression) -> str | None:
    if isinstance(node, exp.Any):
        return "ANY"
    if isinstance(node, exp.All):
        return "ALL"
    if isinstance(node, exp.Anonymous) and node.name.upper() in _QUANTIFIER_FUNCS:
        return node.name.upper()
    return None


def _list_element(nodes: list[exp.Expression], i: int) -> exp.Expression:
    """The list element on the path below nodes[i] (through parentheses and one ARRAY /
    tuple); nodes[i] itself when there is no list."""
    j = i
    while j + 1 < len(nodes) and isinstance(nodes[j], exp.Paren):
        j += 1
    if j + 1 < len(nodes) and isinstance(nodes[j], exp.Array | exp.Tuple):
        return nodes[j + 1]
    return nodes[i]


def _row_element(
    operator: str, nodes: list[exp.Expression], other: exp.Expression
) -> _Operand | None:
    """Element of a row value `(1, 2)`, compared to the matching element of `(a, b)`."""
    row, element = nodes[0], nodes[1]
    if not isinstance(other, exp.Tuple) or len(other.expressions) != len(row.expressions):
        return None
    return _Operand(f"ROW {operator}", element, other.expressions[element.index])


def _in_operand(predicate: exp.In, nodes: list[exp.Expression]) -> _Operand | None:
    head = nodes[0]
    if head.arg_key == "unnest" and len(nodes) > 1:  # x IN UNNEST([...])
        return _Operand("IN UNNEST", _list_element(nodes, 1), predicate.this)
    if not any(e is head for e in predicate.expressions):
        return None
    if isinstance(head, exp.Tuple) and len(nodes) > 1:  # (a, b) IN ((1, 2), ...)
        return _row_element("IN", nodes, predicate.this)
    return _Operand("IN", head, predicate.this)


def _comparison_operand(predicate: exp.Expression, nodes: list[exp.Expression]) -> _Operand:
    head = nodes[0]
    operator = _COMPARISONS[type(predicate)]
    other = predicate.left if head is predicate.right else predicate.right
    if head is predicate.left:  # literal on the left: 5 < x  →  x > 5
        operator = _FLIP.get(operator, operator)
    quantifier = _quantifier(head)
    if quantifier is not None and len(nodes) > 1:  # x = ANY(ARRAY[...]), d ILIKE ANY (...)
        return _Operand(f"{operator} {quantifier}", _list_element(nodes, 1), other)
    if isinstance(head, exp.Tuple) and len(nodes) > 1:  # (a, b) = (1, 2)
        return _row_element(operator, nodes, other) or _Operand(operator, head, other)
    return _Operand(operator, head, other)


def _classify(predicate: exp.Expression, nodes: list[exp.Expression]) -> _Operand | None:
    """The literal's operand when `nodes` (predicate operand first, literal last) is the
    value side of `predicate`."""
    if isinstance(predicate, exp.In):
        operand = _in_operand(predicate, nodes)
    elif isinstance(predicate, exp.Between):
        head = nodes[0]
        if head.arg_key not in ("low", "high"):
            return None
        operand = _Operand(f"BETWEEN_{head.arg_key.upper()}", head, predicate.this)
    else:
        operand = _comparison_operand(predicate, nodes)
    if operand is None or _has_reference(operand.value_root):
        return None
    if operand.other is None or not _has_reference(operand.other):
        return None
    return operand


def _join_on_facts(join: exp.Join) -> tuple[LiteralContext, ...]:
    """Outer joins filter attached rows; semi joins filter like inner joins; anti joins
    keep the rows that do NOT match (like NOT EXISTS)."""
    join_type = _join_type(join, has_condition=True)
    if join_type in ("left", "right", "full"):
        return (LiteralContext.OUTER_JOIN_ON,)
    if join_type == "anti":
        return (LiteralContext.NEGATED, LiteralContext.JOIN_ON)
    return (LiteralContext.JOIN_ON,)


def _ancestor_facts(parent: exp.Expression, child: exp.Expression) -> tuple[LiteralContext, ...]:
    if isinstance(parent, exp.Not):
        return (LiteralContext.NEGATED,)
    if isinstance(parent, exp.Or):
        return (LiteralContext.OR_BRANCH,)
    if isinstance(parent, exp.If) and child.arg_key == "this":
        return (LiteralContext.CASE_CONDITION,)
    if isinstance(parent, exp.Filter) and child.arg_key == "expression":
        return (LiteralContext.CASE_CONDITION,)
    if isinstance(parent, exp.Having):
        return (LiteralContext.HAVING,)
    if isinstance(parent, exp.Join) and child.arg_key == "on":
        return _join_on_facts(parent)
    if isinstance(parent, exp.Subquery | exp.Exists | exp.CTE):
        return (LiteralContext.SUBQUERY,)
    return ()


def _context(located: _Located, scope_root: exp.Expression) -> tuple[LiteralContext, ...]:
    facts: set[LiteralContext] = set()
    node = located.node
    while node is not located.value_root:
        node = node.parent
        if not isinstance(node, exp.Paren):
            facts.add(LiteralContext.VALUE_WRAPPED)
    if located.predicate.args.get("negate"):  # e.g. DuckDB `x NOT LIKE 'a'`
        facts.add(LiteralContext.NEGATED)
    child = located.predicate
    while child is not scope_root and child.parent is not None:
        facts.update(_ancestor_facts(child.parent, child))
        child = child.parent
    return tuple(c for c in LiteralContext if c in facts)


def _format_literal(located: _Located) -> exp.Literal | None:
    node = located.node
    while node is not located.value_root:
        parent = node.parent
        if isinstance(parent, _PARSE_FUNCS) and node.arg_key == "this":
            fmt = parent.args.get("format")
            if isinstance(fmt, exp.Literal) and fmt.is_string:
                return fmt
        node = parent
    return None


def find_literals(tree: exp.Expression) -> list[FoundLiteral]:
    """All filter literals in the tree with stable IDs (see module docstring)."""
    found: list[FoundLiteral] = []
    for scope in scopes(tree):
        ordinal = 0
        for node in _walk(scope, tree):
            if not isinstance(node, exp.Literal | exp.Boolean):
                continue
            located = _locate(node)
            if located is None:
                continue
            ordinal += 1
            found.append(
                FoundLiteral(
                    id=f"{scope.name}.L{ordinal}",
                    scope=scope.name,
                    node=located.node,
                    literal=node,
                    predicate=located.predicate,
                    operator=located.operator,
                    other_side=located.other,
                    value_root=located.value_root,
                    context=_context(located, scope.root),
                    format_literal=_format_literal(located),
                )
            )
    return found


def wrapper_sql(found: FoundLiteral, dialect: Dialect) -> str | None:
    """The value-side wrapper with this literal shown as `?`; None when not wrapped."""
    if not found.value_wrapped:
        return None
    path: list[tuple[str, int | None]] = []
    node = found.node
    while node is not found.value_root:
        path.append((node.arg_key, node.index))
        node = node.parent
    copy = found.value_root.copy()
    target: exp.Expression = copy
    for key, index in reversed(path):
        child = target.args[key]
        target = child[index] if isinstance(child, list) else child
    target.replace(exp.Var(this="?"))
    return copy.sql(dialect=str(dialect))


# ── Schema lookups ──────────────────────────────────────────────────────────────────

_Parts = tuple[tuple[str, bool], ...]  # (identifier, quoted) per qualifier level


def _ident_match(key: str, name: str, quoted: bool) -> bool:
    return key == name if quoted else key.lower() == name.lower()


def _parts_match(key: tuple[str, ...], parts: _Parts) -> bool:
    return all(_ident_match(k, n, q) for k, (n, q) in zip(key, parts, strict=True))


class SchemaIndex:
    """Finds a table's columns. Unquoted identifiers match case-insensitively, quoted ones
    exactly. The fully qualified key wins; otherwise the single key whose overlapping
    trailing parts match (e.g. SQL `analytics.deals` → key `deals`, if unambiguous)."""

    def __init__(self, schema: Schema | None) -> None:
        self._entries = [
            (tuple(p.strip('"`') for p in key.split(".")), columns)
            for key, columns in (schema or {}).items()
        ]

    def columns(self, parts: _Parts) -> dict[str, str] | None:
        exact = [c for k, c in self._entries if len(k) == len(parts) and _parts_match(k, parts)]
        if exact:
            return exact[0]
        loose = [
            c
            for k, c in self._entries
            if _parts_match(k[-min(len(k), len(parts)) :], parts[-min(len(k), len(parts)) :])
        ]
        return loose[0] if len(loose) == 1 else None

    def column_type(self, parts: _Parts, column: exp.Expression) -> str | None:
        columns = self.columns(parts)
        if columns is None:
            return None
        quoted = isinstance(column, exp.Identifier) and column.quoted
        return next((t for c, t in columns.items() if _ident_match(c, column.name, quoted)), None)


def _table_parts(table: exp.Table) -> _Parts:
    if not isinstance(table.this, exp.Identifier):
        return ()
    return tuple(
        (p.name, bool(p.args.get("quoted"))) for p in table.parts if isinstance(p, exp.Identifier)
    )


def _display(parts: _Parts) -> str:
    return ".".join(name if quoted else name.lower() for name, quoted in parts)


# ── Column lineage ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Lineage:
    ctes: dict[str, tuple[str, exp.Expression]]  # lookup key → (name as written, body)
    schema: SchemaIndex
    dialect: Dialect
    with_: exp.With | None = None  # The top-level WITH the CTEs come from.
    positions: dict[str, int] = field(default_factory=dict)  # lookup key → index in WITH
    # Memo of column-side expression types: id(node) → (node, type). One analysis only.
    types: dict[int, tuple[exp.Expression, exp.DataType | None]] = field(default_factory=dict)


@dataclass(frozen=True)
class _Source:
    name: str  # Qualified base table, CTE name, or derived-table alias.
    parts: _Parts | None  # Base table only.
    query: exp.Expression | None  # CTE body or derived-table query.


@dataclass(frozen=True)
class _Resolved:
    ref: ColumnRef
    expr: exp.Expression | None = None  # The computing expression when derived.


def _cte_map(tree: exp.Expression) -> dict[str, tuple[str, exp.Expression]]:
    with_ = _top_with(tree)
    return {
        _ident_key(cte.args["alias"].this): (cte.alias_or_name, cte.this)
        for cte in (with_.expressions if with_ else [])
    }


def _cte_positions(tree: exp.Expression) -> dict[str, int]:
    with_ = _top_with(tree)
    ctes = with_.expressions if with_ else []
    return {_ident_key(cte.args["alias"].this): i for i, cte in enumerate(ctes)}


def _defining_cte(node: exp.Expression, with_: exp.With) -> int | None:
    """Index of the top-level CTE whose body holds `node`; None in the outer query."""
    child = node
    while child.parent is not None:
        if child.parent is with_:
            return child.index
        child = child.parent
    return None


def _cte_visible(node: exp.Expression, key: str, lin: _Lineage) -> bool:
    """A CTE name is visible in the outer query and in CTEs defined after it; a CTE's own
    body and earlier CTEs read the base table of that name. WITH RECURSIVE sees all."""
    if key not in lin.positions or lin.with_ is None:
        return False
    if lin.with_.args.get("recursive"):
        return True
    defining = _defining_cte(node, lin.with_)
    return defining is None or lin.positions[key] < defining


def _source_of(node: exp.Expression, lin: _Lineage) -> _Source | None:
    if isinstance(node, exp.Table):
        parts = _table_parts(node)
        if not parts:
            return None
        if len(parts) == 1 and _cte_visible(node, _ident_key(node.this), lin):
            name, body = lin.ctes[_ident_key(node.this)]
            return _Source(name, None, body)
        return _Source(_display(parts), parts, None)
    if isinstance(node, exp.Subquery):
        return _Source(node.alias_or_name, None, node.this)
    return None


def _alias_key(node: exp.Expression) -> str:
    alias = node.args.get("alias")
    return _ident_key(alias.this if alias is not None else node.this)


def _sources(select: exp.Expression, lin: _Lineage) -> dict[str, _Source]:
    """alias key → source, for the FROM and JOINs of this SELECT."""
    from_ = select.args.get("from_")
    nodes = [from_.this] if from_ is not None else []
    nodes += [j.this for j in select.args.get("joins") or []]
    result: dict[str, _Source] = {}
    for node in nodes:
        source = _source_of(node, lin)
        if source is not None:
            result[_alias_key(node)] = source
    return result


def _has_column(source: _Source, column: exp.Expression, lin: _Lineage) -> bool:
    if source.parts is not None:
        return lin.schema.column_type(source.parts, column) is not None
    names = source.query.named_selects if source.query is not None else []
    return column.name.lower() in {n.lower() for n in names}


def _pick_source(column: exp.Column, sources: dict[str, _Source], lin: _Lineage) -> _Source | None:
    if column.table:
        return sources.get(_ident_key(column.args["table"]))
    if len(sources) == 1:
        return next(iter(sources.values()))
    return next((s for s in sources.values() if _has_column(s, column.this, lin)), None)


def _resolve(column: exp.Column, lin: _Lineage, depth: int = 0) -> _Resolved:
    """Trace a column to its base table (through CTEs and derived tables)."""
    select = column.find_ancestor(exp.Select)
    source = None
    while select is not None and source is None:
        source = _pick_source(column, _sources(select, lin), lin)
        if not column.table:
            break
        select = select.find_ancestor(exp.Select)  # correlated reference to an outer query
    if source is None:
        return _Resolved(ColumnRef(table=None, column=_ident_key(column.this)))
    return _resolve_in(source, column.this, lin, depth)


def _resolve_in(source: _Source, ident: exp.Expression, lin: _Lineage, depth: int) -> _Resolved:
    name = _ident_key(ident)
    if source.parts is not None:
        col_type = lin.schema.column_type(source.parts, ident)
        return _Resolved(ColumnRef(table=source.name, column=name, type=col_type))
    derived = _Resolved(ColumnRef(table=source.name, column=name, derived=True))
    query = source.query
    if depth > _MAX_DEPTH or not isinstance(query, exp.Select):
        return derived
    for projection in query.selects:
        if isinstance(projection, exp.Star):
            inner = list(_sources(query, lin).values())
            if len(inner) == 1:
                return _resolve_in(inner[0], ident, lin, depth + 1)
        elif projection.alias_or_name.lower() == ident.name.lower():
            computed = projection.unalias()
            if isinstance(computed, exp.Column):
                return _resolve(computed, lin, depth + 1)
            return _Resolved(derived.ref, computed)
    return derived


# ── Compared type ───────────────────────────────────────────────────────────────────


def _annotate(expr: exp.Expression, dialect: Dialect) -> exp.DataType | None:
    """sqlglot type annotation, best effort (it has no complete function catalog)."""
    try:
        return annotate_types(expr, dialect=str(dialect)).type
    except (SqlglotError, ValueError, KeyError, TypeError, AttributeError):
        return None


def _expr_type(expr: exp.Expression, lin: _Lineage, depth: int = 0) -> exp.DataType | None:
    """Type of a column-side expression: annotated from its columns' warehouse types.
    Memoized per node, so shared lineage (`x + x + x` over CTE chains) is typed once."""
    cached = lin.types.get(id(expr))
    if cached is not None and cached[0] is expr:
        return cached[1]
    if depth > _MAX_DEPTH:
        return None
    result = _compute_type(expr, lin, depth)
    lin.types[id(expr)] = (expr, result)
    return result


def _compute_type(expr: exp.Expression, lin: _Lineage, depth: int) -> exp.DataType | None:
    if isinstance(expr, exp.Column):
        resolved = _resolve(expr, lin)
        if resolved.ref.type:
            return data_type(resolved.ref.type, lin.dialect)
        return _expr_type(resolved.expr, lin, depth + 1) if resolved.expr is not None else None
    # annotate_types re-resolves bare columns (and forgets types set on them), so each
    # typed column is swapped for CAST(NULL AS <type>), which it types reliably.
    copy = expr.copy()
    for original, clone in zip(
        list(expr.find_all(exp.Column)), list(copy.find_all(exp.Column)), strict=True
    ):
        column_type = _expr_type(original, lin, depth + 1)
        if column_type is not None:
            clone.replace(exp.Cast(this=exp.Null(), to=column_type.copy()))
    annotated = _annotate(copy, lin.dialect)
    if annotated is not None and annotated.this != exp.DataType.Type.UNKNOWN:
        return annotated
    return _preserved_type(expr, lin, depth)


def _preserved_type(expr: exp.Expression, lin: _Lineage, depth: int) -> exp.DataType | None:
    """The column's type when every wrapper between it and `expr` keeps the type."""
    column = expr.find(exp.Column)
    base = _expr_type(column, lin, depth + 1) if column is not None else None
    if column is None or base is None:
        return None
    node = column.parent
    while node is not None:
        keeps = isinstance(node, _TYPE_PRESERVING) or (
            isinstance(node, _ARITHMETIC) and base.this in exp.DataType.NUMERIC_TYPES
        )
        if not keeps:
            return None
        if node is expr:
            return base
        node = node.parent
    return None


def _compared_type(other: exp.Expression, resolved: _Resolved | None, lin: _Lineage) -> str | None:
    if isinstance(other, exp.Column) and resolved is not None and resolved.ref.type:
        return resolved.ref.type
    dtype = _expr_type(other, lin)
    return dtype.sql(dialect=str(lin.dialect)) if dtype is not None else None


# ── Literal infos ───────────────────────────────────────────────────────────────────


def _literal_value(found: FoundLiteral) -> str:
    lit = found.literal
    value = lit.name if isinstance(lit, exp.Literal) else ("TRUE" if lit.this else "FALSE")
    return f"-{value}" if isinstance(found.node, exp.Neg) else value


def _suggested_format(found: FoundLiteral, value: str) -> str | None:
    """sqlglot stores format arguments as Python strftime patterns; keep one only if it
    actually reads the original value."""
    if found.format_literal is None:
        return None
    pattern = found.format_literal.name
    try:
        datetime.strptime(value, pattern)
    except ValueError:
        return None
    return pattern


def _literal_info(found: FoundLiteral, in_list: str | None, lin: _Lineage) -> LiteralInfo:
    dialect = str(lin.dialect)
    other = found.other_side
    column_node = (
        other if isinstance(other, exp.Column) else (other.find(exp.Column) if other else None)
    )
    resolved = _resolve(column_node, lin) if column_node is not None else None
    typed = isinstance(found.node, exp.Cast)
    value = _literal_value(found)
    return LiteralInfo(
        id=found.id,
        scope=found.scope,
        value=value,
        is_string=isinstance(found.literal, exp.Literal) and found.literal.is_string,
        form=LiteralForm.TYPED if typed else LiteralForm.BARE,
        cast_type=found.node.to.sql(dialect=dialect) if typed else None,
        operator=found.operator,
        column_sql=other.sql(dialect=dialect) if other is not None else None,
        column=resolved.ref if resolved is not None else None,
        wrapped=other is not None and not isinstance(other, exp.Column),
        in_list=in_list,
        context=list(found.context),
        wrapper_sql=wrapper_sql(found, lin.dialect),
        suggested_format=_suggested_format(found, value),
        compared_type=_compared_type(other, resolved, lin) if other is not None else None,
    )


def _literal_infos(found: list[FoundLiteral], lin: _Lineage) -> list[LiteralInfo]:
    """LiteralInfo per found literal; IN lists numbered per scope (`<scope>.IN<n>`)."""
    groups: dict[int, str] = {}
    per_scope: dict[str, int] = {}
    infos: list[LiteralInfo] = []
    for f in found:
        in_list = None
        if f.operator == "IN":
            key = id(f.predicate)
            if key not in groups:
                per_scope[f.scope] = per_scope.get(f.scope, 0) + 1
                groups[key] = f"{f.scope}.IN{per_scope[f.scope]}"
            in_list = groups[key]
        infos.append(_literal_info(f, in_list, lin))
    return infos


# ── Range candidates (ADR 0015) ─────────────────────────────────────────────────────


def _chain_top(predicate: exp.Expression) -> exp.Expression | None:
    """Top of the AND chain the predicate is a direct operand of (through parentheses)."""
    node = predicate
    while isinstance(node.parent, exp.And | exp.Paren):
        node = node.parent
    return node if node is not predicate else None


def _candidate(
    low: FoundLiteral, high: FoundLiteral, shape: RangeShape, infos: dict[str, LiteralInfo]
) -> RangeCandidate:
    return RangeCandidate(
        scope=low.scope,
        column_sql=infos[low.id].column_sql or "",
        start=low.id,
        end=high.id,
        shape=shape,
    )


def _between_pair(lits: list[FoundLiteral]) -> tuple[FoundLiteral, FoundLiteral] | None:
    lows = [f for f in lits if f.operator == "BETWEEN_LOW"]
    highs = [f for f in lits if f.operator == "BETWEEN_HIGH"]
    return (lows[0], highs[0]) if len(lows) == 1 and len(highs) == 1 else None


def _ranges(found: list[FoundLiteral], infos: dict[str, LiteralInfo]) -> list[RangeCandidate]:
    """Pair bounds that are direct operands of one AND chain (or one BETWEEN), on the same
    column expression, each bound holding exactly one literal."""
    by_predicate: dict[int, list[FoundLiteral]] = {}
    for f in found:
        by_predicate.setdefault(id(f.predicate), []).append(f)
    candidates: list[RangeCandidate] = []
    chains: dict[tuple[str, int, str | None], dict[str, list[FoundLiteral]]] = {}
    for lits in by_predicate.values():
        first = lits[0]
        if isinstance(first.predicate, exp.Between):
            pair = _between_pair(lits)
            if pair is not None:
                candidates.append(_candidate(*pair, RangeShape.INCLUSIVE, infos))
            continue
        top = _chain_top(first.predicate)
        if len(lits) != 1 or first.operator not in _LOW_OPS | _HIGH_OPS or top is None:
            continue
        side = "low" if first.operator in _LOW_OPS else "high"
        key = (first.scope, id(top), infos[first.id].column_sql)
        chains.setdefault(key, {"low": [], "high": []})[side].append(first)
    for bounds in chains.values():
        if len(bounds["low"]) == 1 and len(bounds["high"]) == 1:
            low, high = bounds["low"][0], bounds["high"][0]
            candidates.append(_candidate(low, high, _SHAPES[(low.operator, high.operator)], infos))
    order = {f.id: i for i, f in enumerate(found)}
    return sorted(candidates, key=lambda c: order[c.start])


# ── Joins ───────────────────────────────────────────────────────────────────────────


def _and_operands(condition: exp.Expression | None) -> list[exp.Expression]:
    if condition is None:
        return []
    if isinstance(condition, exp.Paren):
        return _and_operands(condition.this)
    if isinstance(condition, exp.And):
        return _and_operands(condition.left) + _and_operands(condition.right)
    return [condition]


def _column_source(column: exp.Column, sources: dict[str, _Source], lin: _Lineage) -> str | None:
    if column.table:
        key = _ident_key(column.args["table"])
        return key if key in sources else None
    owners = [k for k, s in sources.items() if _has_column(s, column.this, lin)]
    return owners[0] if len(owners) == 1 else None


def _join_keys(
    on: exp.Expression | None, right: str, sources: dict[str, _Source], lin: _Lineage
) -> tuple[str | None, list[tuple[str, str]]]:
    """(left alias key, equality pairs) from the ON condition's top-level AND chain."""
    left: str | None = None
    keys: list[tuple[str, str]] = []
    for operand in _and_operands(on):
        if not isinstance(operand, exp.EQ):
            continue
        a, b = operand.left, operand.right
        if not (isinstance(a, exp.Column) and isinstance(b, exp.Column)):
            continue
        sa, sb = _column_source(a, sources, lin), _column_source(b, sources, lin)
        if sb == right and sa not in (None, right):
            lcol, rcol, lkey = a, b, sa
        elif sa == right and sb not in (None, right):
            lcol, rcol, lkey = b, a, sb
        else:
            continue
        left = left or lkey
        if lkey == left:
            keys.append((_ident_key(lcol.this), _ident_key(rcol.this)))
    return left, keys


def _join_type(join: exp.Join, has_condition: bool) -> str:
    side, kind = (join.side or "").lower(), (join.kind or "").lower()
    if kind in ("semi", "anti"):  # `LEFT SEMI JOIN` filters the driving rows: not outer
        return kind
    if side:
        return side
    if kind == "cross":
        return kind
    return "inner" if has_condition else "cross"


def _join_info(join: exp.Join, scope: str, lin: _Lineage) -> JoinInfo | None:
    select = join.parent
    sources = _sources(select, lin)
    right_key = _alias_key(join.this)
    on, using = join.args.get("on"), join.args.get("using") or []
    if using:
        left_key = None
        keys = [(_ident_key(u), _ident_key(u)) for u in using]
        on_sql = f"USING ({', '.join(u.sql(dialect=str(lin.dialect)) for u in using)})"
    else:
        left_key, keys = _join_keys(on, right_key, sources, lin)
        on_sql = on.sql(dialect=str(lin.dialect)) if on is not None else ""
    from_ = select.args.get("from_")
    left_key = left_key or (_alias_key(from_.this) if from_ is not None else None)
    left, right = sources.get(left_key or ""), sources.get(right_key)
    if left is None or right is None:
        return None
    return JoinInfo(
        scope=scope,
        left_table=left.name,
        right_table=right.name,
        join_type=_join_type(join, on is not None or bool(using)),
        keys=keys,
        on_sql=on_sql,
    )


def _joins(tree: exp.Expression, lin: _Lineage) -> list[JoinInfo]:
    joins: list[JoinInfo] = []
    for scope in scopes(tree):
        for node in _walk(scope, tree):
            if isinstance(node, exp.Join) and isinstance(node.parent, exp.Select):
                info = _join_info(node, scope.name, lin)
                if info is not None:
                    joins.append(info)
    return joins


# ── Public API ──────────────────────────────────────────────────────────────────────


def _table_names(root: exp.Expression, lin: _Lineage, *, include_ctes: bool) -> list[str]:
    names: set[str] = set()
    for table in root.find_all(exp.Table):
        source = _source_of(table, lin)
        if source is not None and (include_ctes or source.parts is not None):
            names.add(source.name)
    return sorted(names)


def _lineage(tree: exp.Expression, dialect: Dialect, schema: Schema | None) -> _Lineage:
    return _Lineage(
        ctes=_cte_map(tree),
        schema=SchemaIndex(schema),
        dialect=dialect,
        with_=_top_with(tree),
        positions=_cte_positions(tree),
    )


def referenced_tables(sql: str, dialect: Dialect) -> list[str]:
    """Base tables a query reads (CTE names excluded), qualified as written with unquoted
    parts lower-cased. Used to fetch the schema first."""
    tree = parse(sql, dialect)
    return _table_names(tree, _lineage(tree, dialect, None), include_ctes=False)


def analyze(sql: str, dialect: Dialect, schema: Schema | None = None) -> QueryAnalysis:
    tree = parse(sql, dialect)
    lin = _lineage(tree, dialect, schema)
    found = find_literals(tree)
    literals = _literal_infos(found, lin)
    return QueryAnalysis(
        dialect=dialect,
        ctes=[
            CteInfo(
                name=name,
                reads=_table_names(body, lin, include_ctes=True),
                outputs=list(body.named_selects),
            )
            for name, body in lin.ctes.values()
        ],
        tables=_table_names(tree, lin, include_ctes=False),
        outputs=list(tree.named_selects),
        literals=literals,
        ranges=_ranges(found, {lit.id: lit for lit in literals}),
        joins=_joins(tree, lin),
    )
