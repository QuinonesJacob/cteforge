"""Checks for composition (DESIGN §17): final-SELECT validation, table resolution, join-key
and grain (fan-out) checks between parts. Pure; works on sqlglot trees.

Name rules (documented contract, shared with assemble.py):
- Names are compared case-insensitively (lowercased), quoted or not.
- Only UNQUALIFIED table references can name a CTE or an alias; `schema.t` is always a table.
- A top-level CTE is visible to the main query and to the bodies of LATER CTEs only. A
  reference inside a CTE's own body (`WITH orders AS (SELECT * FROM orders)`) or to a later
  CTE is the real table, as in DuckDB/Postgres.
- A name defined by a WITH nested inside a subquery shadows outer names inside that subquery,
  with the same rule: not inside that nested CTE's own body or an earlier CTE of its WITH.
- Table functions in FROM/JOIN (`read_csv(...)`, `dblink(...)`, `TABLE(RESULT_SCAN(...))`)
  are reads the parts don't vouch for: they are listed in `raw_tables` as `name()`, except
  pure row generators (UNNEST, GENERATE_SERIES, ...).
- Two CTEs of one WITH whose names differ only in case are a RenderError (see assemble.py).
- `unknown_alias` (error) is reported only for a QUALIFIED column reference (`x.col`) whose
  qualifier `x` is not a source visible in its scope (FROM/JOIN alias or table name, CTE, or
  — for subqueries — an enclosing query's source). Unqualified columns can't be checked
  without schemas, and anything in FROM that is not an alias/CTE is treated as a raw table
  (listed in `raw_tables`), never an unknown alias. Three-part `a.b.col` references are not
  checked, and a qualifier that equals an output column of a visible part is assumed to be a
  struct field access (BigQuery `rec.field`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import BaseModel, ConfigDict
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.scope import Scope, ScopeType, traverse_scope

from cteforge.domain.errors import RenderError
from cteforge.domain.issues import Issue, IssueCode, Severity
from cteforge.domain.models import Definition


class JoinUse(BaseModel):
    """A join written in the final SELECT (DESIGN §17)."""

    model_config = ConfigDict(frozen=True)

    left: str  # alias, raw table name, or the final SELECT's own CTE / subquery alias
    right: str
    keys: list[tuple[str, str]]  # (left_col, right_col) equality pairs
    known: bool  # both sides are aliases and every key is in both sides' join_keys


# ── Read-only validation ────────────────────────────────────────────────────────────

_NOT_READ_ONLY: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Command,
    exp.TruncateTable,
    exp.Copy,
    exp.Grant,
    exp.Use,
    exp.Set,
    exp.Pragma,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Analyze,
    exp.LoadData,
    exp.Cache,
    exp.Uncache,
    exp.Refresh,
    exp.Into,
    exp.Lock,
)


# Functions that change state, run SQL given as a string, reach outside the warehouse's
# tables (server files, environment, other connections) or stall the session. A SELECT that
# calls one is not read-only even though its statement type is SELECT. Matched by lowercased
# name; Snowflake `SYSTEM$...` functions are matched by prefix. Not exhaustive: the warehouse
# connection must still use a read-only role (DESIGN §14).
_SIDE_EFFECT_FUNCTIONS = frozenset(
    {
        # Postgres: sessions, settings, sequences, locks, notifications
        "pg_terminate_backend",
        "pg_cancel_backend",
        "pg_reload_conf",
        "pg_rotate_logfile",
        "pg_switch_wal",
        "pg_create_restore_point",
        "pg_promote",
        "pg_stat_reset",
        "pg_stat_reset_shared",
        "pg_stat_reset_single_table_counters",
        "pg_stat_reset_single_function_counters",
        "set_config",
        "nextval",
        "setval",
        "pg_advisory_lock",
        "pg_advisory_lock_shared",
        "pg_advisory_xact_lock",
        "pg_advisory_xact_lock_shared",
        "pg_try_advisory_lock",
        "pg_try_advisory_lock_shared",
        "pg_try_advisory_xact_lock",
        "pg_try_advisory_xact_lock_shared",
        "pg_advisory_unlock",
        "pg_advisory_unlock_shared",
        "pg_advisory_unlock_all",
        "pg_notify",
        "pg_sleep",
        "pg_sleep_for",
        "pg_sleep_until",
        "pg_logical_emit_message",
        "pg_create_logical_replication_slot",
        "pg_create_physical_replication_slot",
        "pg_drop_replication_slot",
        "pg_replication_origin_create",
        # Postgres: server files and large objects
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_stat_file",
        "lo_import",
        "lo_export",
        "lo_unlink",
        "lo_create",
        "lo_creat",
        "lo_put",
        "lo_from_bytea",
        # Postgres: other connections / dynamic SQL
        "dblink",
        "dblink_exec",
        "dblink_connect",
        "dblink_connect_u",
        "dblink_open",
        "dblink_send_query",
        "query_to_xml",
        "query_to_xmlschema",
        "query_to_xml_and_xmlschema",
        "cursor_to_xml",
        # DuckDB: dynamic SQL, environment
        "query",
        "query_table",
        "getenv",
    }
)
_SIDE_EFFECT_PREFIXES = ("system$",)  # Snowflake SYSTEM$CANCEL_QUERY, SYSTEM$ABORT_SESSION...


def function_name(func: exp.Func) -> str:
    """Lowercased SQL name of a function node (`read_csv`, `nextval`, `system$cancel_query`)."""
    if isinstance(func, exp.Anonymous):
        return func.name.lower()
    return func.sql_name().lower()


def _side_effect(func: exp.Func) -> bool:
    name = function_name(func)
    return name in _SIDE_EFFECT_FUNCTIONS or name.startswith(_SIDE_EFFECT_PREFIXES)


def ensure_query(tree: exp.Expression, what: str) -> exp.Query:
    """Return `tree` as one read-only SELECT/UNION, or raise RenderError.

    Rejects DML/DDL anywhere in the tree (incl. data-modifying CTEs), SELECT ... INTO,
    row locks (FOR UPDATE), calls to functions with side effects (`_SIDE_EFFECT_FUNCTIONS`)
    and WITH RECURSIVE (unsupported in composition).
    """
    while isinstance(tree, exp.Subquery) and not tree.alias and isinstance(tree.this, exp.Query):
        tree = tree.this  # a parenthesized query: (SELECT ...)
    if not isinstance(tree, (exp.Select, exp.SetOperation)):
        raise RenderError(
            f"The {what} must be a single SELECT (or UNION of SELECTs); "
            f"got {tree.key.upper()}. Composition only builds read-only queries."
        )
    for node in tree.walk():
        if isinstance(node, _NOT_READ_ONLY):
            raise RenderError(
                f"The {what} contains {node.key.upper()}, which is not read-only. "
                "Composition only builds read-only SELECT queries."
            )
        if isinstance(node, exp.Func) and _side_effect(node):
            raise RenderError(
                f"The {what} calls {function_name(node)}(), which can change state, run other "
                "SQL, or read outside the warehouse's tables. Composition only builds "
                "read-only SELECT queries."
            )
        if isinstance(node, exp.With) and node.args.get("recursive"):
            raise RenderError(
                f"The {what} uses WITH RECURSIVE, which composition does not support. "
                "Run it on its own instead of combining it with other definitions."
            )
    return tree


# ── Table resolution ────────────────────────────────────────────────────────────────


class RefKind(StrEnum):
    CTE = "cte"  # a top-level CTE of this tree
    NESTED = "nested"  # a CTE of a WITH nested inside a subquery
    TABLE = "table"  # anything else: a real table (or, in the final SELECT, maybe an alias)


@dataclass(frozen=True)
class TableRef:
    node: exp.Table
    kind: RefKind
    key: str  # lowercased unqualified name for CTE/NESTED/unqualified TABLE; else qualified


def top_with(tree: exp.Expression) -> exp.With | None:
    with_ = tree.args.get("with_")
    return with_ if isinstance(with_, exp.With) else None


def cte_names(tree: exp.Expression) -> list[str]:
    """Lowercased names of the tree's top-level CTEs, in order."""
    with_ = top_with(tree)
    return [cte.alias_or_name.lower() for cte in with_.expressions] if with_ else []


def qualified_name(table: exp.Table) -> str:
    return ".".join(part.name for part in table.parts)


def is_unqualified(table: exp.Table) -> bool:
    return not table.args.get("db") and not table.args.get("catalog")


def table_refs(tree: exp.Expression) -> list[TableRef]:
    """Every named table reference in `tree`, classified by what it resolves to."""
    top = top_with(tree)
    top_ctes = top.expressions if top else []
    top_names = [c.alias_or_name.lower() for c in top_ctes]
    refs: list[TableRef] = []
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            continue  # table functions, UNNEST-like sources
        if not is_unqualified(table):
            refs.append(TableRef(table, RefKind.TABLE, qualified_name(table)))
            continue
        name = table.name.lower()
        if _nested_shadow(table, name, tree):
            refs.append(TableRef(table, RefKind.NESTED, name))
            continue
        visible = top_names
        owner = _owning_top_cte(table, top)
        if owner is not None:
            visible = top_names[: top_ctes.index(owner)]  # only earlier CTEs
        kind = RefKind.CTE if name in visible else RefKind.TABLE
        refs.append(TableRef(table, kind, name))
    return refs


def _nested_shadow(node: exp.Expression, name: str, root: exp.Expression) -> bool:
    """True when a WITH nested below `root` defines `name` and it is visible at `node`.

    Same visibility as the top level: a nested CTE is visible to its query and to LATER CTEs
    of the same WITH, not to its own body or earlier ones (those see the outer name).
    """
    parent = node.parent
    while parent is not None and parent is not root:
        with_ = top_with(parent)
        if with_ is not None:
            ctes = with_.expressions
            names = [c.alias_or_name.lower() for c in ctes]
            owner = _owning_top_cte(node, with_)
            if owner is not None:
                names = names[: ctes.index(owner)]
            if name in names:
                return True
        parent = parent.parent
    return False


def _owning_top_cte(node: exp.Expression, top: exp.With | None) -> exp.CTE | None:
    if top is None:
        return None
    child: exp.Expression = node
    parent = node.parent
    while parent is not None:
        if parent is top and isinstance(child, exp.CTE):
            return child
        child, parent = parent, parent.parent
    return None


# Table functions that only generate rows from their arguments (no outside data).
_ROW_GENERATORS = frozenset({"unnest", "explode", "generate_series", "range", "generate_array"})
_ROW_GENERATORS_PREFIXES = ("generate_",)  # BigQuery GENERATE_DATE_ARRAY, ...


def table_function(node: exp.Expression) -> exp.Func | None:
    """The function a FROM/JOIN source calls (`read_csv(...)`, `TABLE(RESULT_SCAN(...))`,
    `LATERAL FLATTEN(...)`, `UNNEST(...)`), or None for a named table / subquery."""
    if isinstance(node, exp.Func):
        return node
    if isinstance(node, (exp.Table, exp.TableFromRows, exp.Lateral)):
        inner = node.this
        if isinstance(inner, exp.Func):
            return inner
        if isinstance(node, exp.Table):  # Postgres ROWS FROM (f(), g())
            for item in node.args.get("rows_from") or []:
                found = table_function(item)
                if found is not None:
                    return found
    return None


def external_table_functions(tree: exp.Expression) -> list[str]:
    """`name()` for every table function in `tree` that may read data from outside the
    warehouse tables the query names (files, other connections, earlier results).

    Pure row generators (UNNEST, GENERATE_SERIES, ...) are not listed. These go in
    `raw_tables`: they are reads the parts don't vouch for.
    """
    names: list[str] = []
    for func in tree.find_all(exp.Func):
        parent = func.parent
        is_source = isinstance(parent, (exp.Table, exp.TableFromRows, exp.Lateral)) and (
            parent.this is func
        )
        if not is_source:
            continue
        name = function_name(func)
        if name in _ROW_GENERATORS or name.startswith(_ROW_GENERATORS_PREFIXES):
            continue
        names.append(f"{name}()")
    return names


# ── Qualifier check (unknown_alias) ─────────────────────────────────────────────────


def check_qualifiers(tree: exp.Expression, defs: dict[str, Definition]) -> list[Issue]:
    """`unknown_alias` errors for qualified columns whose qualifier names no visible source."""
    try:
        scopes = traverse_scope(tree)
    except SqlglotError:
        return []  # sqlglot can't build scopes for this shape; nothing reliable to report
    struct_like = {o.lower() for d in defs.values() for o in d.outputs}
    issues: list[Issue] = []
    seen: set[str] = set()
    for scope in scopes:
        visible = _visible_sources(scope)
        for column in scope.columns:
            qualifier = column.table
            if not qualifier or column.args.get("db") or column.args.get("catalog"):
                continue
            q = qualifier.lower()
            if q in visible or q in struct_like or q in seen:
                continue
            seen.add(q)
            issues.append(
                Issue(
                    code=IssueCode.UNKNOWN_ALIAS,
                    severity=Severity.ERROR,
                    message=(
                        f"'{qualifier}.{column.name}' refers to '{qualifier}', which is not "
                        "a part alias, CTE, table or table alias visible there. Available "
                        f"aliases: {', '.join(sorted(defs)) or 'none'}."
                    ),
                )
            )
    return issues


def _visible_sources(scope: Scope) -> set[str]:
    names = {name.lower() for name in scope.sources}
    current: Scope | None = scope
    while current is not None and current.scope_type not in (ScopeType.CTE, ScopeType.ROOT):
        current = current.parent  # subqueries / derived tables may correlate outward
        if current is not None:
            names |= {name.lower() for name in current.sources}
    return names


# ── Joins ───────────────────────────────────────────────────────────────────────────


@dataclass
class _Source:
    key: str  # how columns qualify it: table alias or table name, lowercased
    name: str  # what JoinUse reports
    alias: str | None  # part alias when it reads a part


@dataclass
class _Join:
    select: exp.Select
    left: _Source
    right: _Source
    keys: list[tuple[str, str]] = field(default_factory=list)


def analyze_joins(
    tree: exp.Expression, defs: dict[str, Definition], refs: list[TableRef]
) -> tuple[list[JoinUse], list[Issue]]:
    """JoinUse per join in `tree` (every SELECT in it), plus unknown_join / fan_out_risk.

    `refs` is `table_refs(tree)`; a TABLE ref whose key is in `defs` reads that part.
    """
    by_node = {id(r.node): r for r in refs}
    found: list[_Join] = []
    for select in tree.find_all(exp.Select):
        joins = select.args.get("joins") or []
        from_ = select.args.get("from_")
        if not joins or from_ is None:
            continue
        sources = [_source(from_.this, defs, by_node)]
        for join in joins:
            right = _source(join.this, defs, by_node)
            found.extend(_join_uses(select, join, sources, right, defs))
            sources.append(right)
    uses = [
        JoinUse(left=j.left.name, right=j.right.name, keys=j.keys, known=_known(j, defs))
        for j in found
    ]
    return uses, _join_issues(found, uses, defs)


def _source(node: exp.Expression, defs: dict[str, Definition], by_node: dict) -> _Source:
    key = (node.alias_or_name or "").lower()
    if isinstance(node, exp.Table) and id(node) in by_node:
        ref: TableRef = by_node[id(node)]
        if ref.kind is RefKind.TABLE and ref.key in defs:
            return _Source(key, ref.key, ref.key)
        name = ref.key if ref.kind is not RefKind.TABLE else qualified_name(node)
        return _Source(key, name, None)
    func = table_function(node)
    if func is not None:
        return _Source(key, f"{function_name(func)}()", None)
    return _Source(key, node.alias_or_name or node.key, None)


def _conjuncts(condition: exp.Expression | None) -> list[exp.Expression]:
    if condition is None:
        return []
    return list(condition.flatten()) if isinstance(condition, exp.And) else [condition]


def _side(
    column: exp.Expression, candidates: list[_Source], defs: dict[str, Definition]
) -> _Source | None:
    if not isinstance(column, exp.Column) or not isinstance(column.this, exp.Identifier):
        return None
    if column.table:
        q = column.table.lower()
        return next((s for s in reversed(candidates) if s.key == q), None)
    name = column.name.lower()
    owners = [
        s for s in candidates if s.alias and name in {o.lower() for o in defs[s.alias].outputs}
    ]
    return owners[0] if len(owners) == 1 else None


def _join_uses(
    select: exp.Select,
    join: exp.Join,
    preceding: list[_Source],
    right: _Source,
    defs: dict[str, Definition],
) -> list[_Join]:
    using = join.args.get("using") or []
    if using:
        cols = [c.name for c in using]
        left = _using_left(cols, preceding, defs)
        return [_Join(select, left, right, [(c, c) for c in cols])]
    condition = join.args.get("on")
    if condition is None:  # comma / cross join: equalities may sit in WHERE
        where = select.args.get("where")
        condition = where.this if where is not None else None
    grouped: dict[int, _Join] = {}
    candidates = [*preceding, right]
    for pred in _conjuncts(condition):
        if not isinstance(pred, exp.EQ):
            continue
        a, b = _side(pred.this, candidates, defs), _side(pred.expression, candidates, defs)
        if a is None or b is None or a is b:
            continue
        if b is right and a is not right:
            other, lcol, rcol = a, pred.this.name, pred.expression.name
        elif a is right:
            other, lcol, rcol = b, pred.expression.name, pred.this.name
        else:
            continue  # equality between two earlier sources: belongs to an earlier join
        entry = grouped.setdefault(id(other), _Join(select, other, right))
        entry.keys.append((lcol, rcol))
    if grouped:
        return list(grouped.values())
    return [_Join(select, preceding[0], right)]


def _using_left(cols: list[str], preceding: list[_Source], defs: dict[str, Definition]) -> _Source:
    if len(preceding) == 1:
        return preceding[0]
    wanted = {c.lower() for c in cols}
    for source in preceding:
        if source.alias and wanted <= {o.lower() for o in defs[source.alias].outputs}:
            return source
    return preceding[0]


def _known(join: _Join, defs: dict[str, Definition]) -> bool:
    if not (join.left.alias and join.right.alias and join.keys):
        return False
    # join_keys are plain column names, so the only pairing they declare is "same key on
    # both sides": `a.account_id = b.user_id` is not known even if both columns are keys.
    left_keys = {k.lower() for k in defs[join.left.alias].join_keys}
    right_keys = {k.lower() for k in defs[join.right.alias].join_keys}
    return all(
        lc.lower() == rc.lower() and lc.lower() in left_keys and rc.lower() in right_keys
        for lc, rc in join.keys
    )


def _grain(definition: Definition) -> str | None:
    if definition.grain is None or not definition.grain.strip():
        return None
    return " ".join(definition.grain.lower().split())


def _join_issues(
    found: list[_Join], uses: list[JoinUse], defs: dict[str, Definition]
) -> list[Issue]:
    issues: list[Issue] = []
    fan_pairs: set[tuple[str, str]] = set()
    for join, use in zip(found, uses, strict=True):
        left, right = join.left.alias, join.right.alias
        if not (left and right):
            continue
        if not use.known:
            on = " AND ".join(f"{left}.{lc} = {right}.{rc}" for lc, rc in use.keys) or "no keys"
            issues.append(
                Issue(
                    code=IssueCode.UNKNOWN_JOIN,
                    severity=Severity.WARNING,
                    message=(
                        f"Join between '{left}' and '{right}' ({on}) does not use declared "
                        f"join keys ({left}: {defs[left].join_keys or 'none'}; "
                        f"{right}: {defs[right].join_keys or 'none'}). "
                        "Check the relationship before trusting the result."
                    ),
                )
            )
        lg, rg = _grain(defs[left]), _grain(defs[right])
        pair = (min(left, right), max(left, right))
        if lg and rg and lg != rg and pair not in fan_pairs:
            fan_pairs.add(pair)
            issues.append(
                Issue(
                    code=IssueCode.FAN_OUT_RISK,
                    severity=Severity.WARNING,
                    message=(
                        f"'{left}' ({defs[left].grain}) and '{right}' ({defs[right].grain}) "
                        "have different grains and are joined directly; rows of the coarser "
                        "one repeat, so SUM/COUNT over its columns can be inflated even if "
                        "you aggregate after the join. Aggregate to a common grain (GROUP BY "
                        "in a subquery or CTE) before joining."
                    ),
                )
            )
    return issues
