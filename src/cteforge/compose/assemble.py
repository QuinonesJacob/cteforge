"""compose(): combine rendered definitions and an optional final SELECT (DESIGN §17). Pure.

Layout of the composed query (deterministic, dependencies before use):

    WITH <part1 inner CTEs as part1__<cte>>, part1 AS (<part1 main query>),
         <part2 ...>, part2 AS (...),
         <the final SELECT's own CTEs>
    <final SELECT>

Naming (see checks.py for the resolution rules):
- Hoisted names are `<alias>__<cte>` (lowercased; characters outside [a-z0-9_] become `_`).
  If that name is already taken (another hoisted CTE, an alias, the final SELECT's CTEs, or
  an unqualified table read by any part or the final SELECT) a suffix `_2`, `_3`, ... is
  added. A renamed reference with no table alias gets the old name as its alias, so column
  qualifiers keep working.
- The final SELECT's own CTEs keep their names unless taken, then get a suffix the same way.
- Unavoidable collisions raise RenderError: an alias equal to an unqualified table some part
  reads (the alias CTE would shadow it), a final-SELECT CTE named like an alias, or two
  top-level CTEs of one tree whose names differ only in case (quoted `"Recent"` vs `recent`).
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict
from sqlglot import exp

from cteforge.compose.checks import (
    JoinUse,
    RefKind,
    TableRef,
    analyze_joins,
    check_qualifiers,
    cte_names,
    dictionary_reads,
    ensure_query,
    external_table_functions,
    query_ctes,
    table_refs,
    top_with,
)
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import RenderError, SqlParseError
from cteforge.domain.issues import Issue, IssueCode, Severity
from cteforge.domain.models import Definition, Ident
from cteforge.parsing.analyze import generate, parse

_UNSAFE = re.compile(r"[^a-z0-9_]")


class Part(BaseModel):
    model_config = ConfigDict(frozen=True)

    alias: Ident  # name the final SELECT uses
    definition: Definition  # for grain / join_keys / outputs
    sql: str  # this definition already rendered (templating) in the compose dialect


class ComposedQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    sql: str
    dialect: Dialect
    issues: list[Issue]
    raw_tables: list[str]  # tables the final SELECT reads that aren't aliases
    joins: list[JoinUse]


def compose(parts: list[Part], select: str | None, dialect: Dialect) -> ComposedQuery:
    """Build one query from `parts` (each rendered in `dialect`) and a final SELECT.

    Raises RenderError for unusable input (bad SQL, not read-only, WITH RECURSIVE, duplicate
    aliases, unavoidable name collisions). Problems the caller can act on are Issues.
    """
    if select is None or not select.strip():
        if len(parts) != 1:
            raise RenderError(
                f"Without a final SELECT, exactly one definition can be used; got {len(parts)}."
                " Write a SELECT over the aliases to combine several."
            )
        ensure_query(_parse(parts[0].sql, dialect, f"part '{parts[0].alias}'"), "definition")
        return ComposedQuery(sql=parts[0].sql, dialect=dialect, issues=[], raw_tables=[], joins=[])
    if not parts:
        raise RenderError("compose needs at least one definition (part).")
    defs = _definitions(parts)

    trees = [
        ensure_query(_parse(p.sql, dialect, f"part '{p.alias}'"), f"definition '{p.alias}'")
        for p in parts
    ]
    final = ensure_query(_parse(select, dialect, "final SELECT"), "final SELECT")
    for p, tree in zip(parts, trees, strict=True):
        _check_distinct_ctes(tree, f"definition '{p.alias}'")
    _check_distinct_ctes(final, "final SELECT")
    part_refs = [table_refs(t) for t in trees]
    final_refs = table_refs(final)
    final_ctes = cte_names(final)

    _check_unavoidable(parts, part_refs, final_ctes)

    taken: set[str] = set(defs)
    for refs in [*part_refs, final_refs]:
        taken |= {r.key for r in refs if r.kind is RefKind.TABLE and "." not in r.key}

    # The final SELECT's own CTEs first (they keep their names when possible), then hoisting.
    final_names = {name: _allocate(name, taken) for name in final_ctes}
    _rename(final, final_refs, final_names)
    alias_refs = _normalize_alias_refs(final_refs, defs)

    ctes: list[exp.CTE] = []
    for part, tree, refs in zip(parts, trees, part_refs, strict=True):
        names = {c: _allocate(f"{part.alias}__{c}", taken) for c in cte_names(tree)}
        _rename(tree, refs, names)
        with_ = top_with(tree)
        if with_ is not None:
            ctes.extend(with_.expressions)
            tree.set("with_", None)
        ctes.append(_cte(part.alias, tree))
    own_with = top_with(final)
    if own_with is not None:
        ctes.extend(own_with.expressions)
    # Checks run on the final SELECT alone, before its WITH holds the hoisted parts.
    issues = check_qualifiers(final, defs)
    issues += _unused(parts, alias_refs)
    joins, join_issues = analyze_joins(final, defs, final_refs)
    issues += join_issues
    raw_tables = sorted(
        {r.key if "." in r.key else r.node.name for r in final_refs if _is_raw(r, defs)}
        | set(external_table_functions(final))
        | set(dictionary_reads(final))
    )

    final.set("with_", exp.With(expressions=ctes))
    sql = generate(final, dialect)
    return ComposedQuery(
        sql=sql, dialect=dialect, issues=issues, raw_tables=raw_tables, joins=joins
    )


# ── Helpers ─────────────────────────────────────────────────────────────────────────


def _parse(sql: str, dialect: Dialect, what: str) -> exp.Expression:
    try:
        return parse(sql, dialect)
    except SqlParseError as exc:
        raise RenderError(f"The {what} is not one valid {dialect} statement: {exc}") from exc


def _definitions(parts: list[Part]) -> dict[str, Definition]:
    defs: dict[str, Definition] = {}
    for part in parts:
        if part.alias in defs:
            raise RenderError(f"Alias '{part.alias}' is used twice; give each part its own alias.")
        defs[part.alias] = part.definition
    return defs


def _check_distinct_ctes(tree: exp.Expression, what: str) -> None:
    """Names compare case-insensitively, so `"Recent"` and `recent` in one WITH can't both be
    hoisted; Snowflake/Postgres would treat them as two CTEs."""
    seen: dict[str, str] = {}
    with_ = top_with(tree)
    for cte in with_.expressions if with_ else []:
        name = cte.alias_or_name
        if name.lower() in seen:
            raise RenderError(
                f"The {what} defines CTEs '{seen[name.lower()]}' and '{name}', whose names "
                "differ only in case. Composition compares names case-insensitively; rename "
                "one of them."
            )
        seen[name.lower()] = name


def _check_unavoidable(
    parts: list[Part], part_refs: list[list[TableRef]], final_ctes: list[str]
) -> None:
    aliases = {p.alias for p in parts}
    for part, refs in zip(parts, part_refs, strict=True):
        for ref in refs:
            if ref.kind is RefKind.TABLE and ref.key in aliases:
                raise RenderError(
                    f"Alias '{ref.key}' is also the name of a table that definition "
                    f"'{part.alias}' reads; the alias would hide that table. "
                    "Choose a different alias."
                )
    for name in final_ctes:
        if name in aliases:
            raise RenderError(
                f"The final SELECT defines a CTE named '{name}', which is also a part alias. "
                "Rename the CTE or the alias."
            )


def _allocate(base: str, taken: set[str]) -> str:
    base = _UNSAFE.sub("_", base.lower())
    name, n = base, 2
    while name in taken:
        name, n = f"{base}_{n}", n + 1
    taken.add(name)
    return name


def _rename(tree: exp.Expression, refs: list[TableRef], names: dict[str, str]) -> None:
    """Rename the tree's top-level query CTEs and every reference to them, all to the same
    UNQUOTED name. `table_refs` matches top-level CTEs case-insensitively; writing both sides
    alike is what makes the warehouse agree (Snowflake `WITH "users" ... FROM users` would
    otherwise read the real table USERS)."""
    for ref in refs:
        if ref.kind is RefKind.CTE:
            new = names.get(ref.key, ref.key)
            if new != ref.node.name or ref.node.this.quoted:
                _retarget(ref.node, new)
    for cte in query_ctes(top_with(tree)):
        new = names[cte.alias_or_name.lower()]
        alias = cte.args["alias"]
        if new != cte.alias_or_name or alias.this.quoted:
            alias.set("this", exp.to_identifier(new))


def _retarget(table: exp.Table, name: str) -> None:
    original = table.this
    table.set("this", exp.to_identifier(name))
    if not table.alias:
        table.set("alias", exp.TableAlias(this=original.copy()))


def _normalize_alias_refs(refs: list[TableRef], defs: dict[str, Definition]) -> set[str]:
    """Point final-SELECT references at the alias CTEs; return the aliases used."""
    used: set[str] = set()
    for ref in refs:
        if ref.kind is RefKind.TABLE and ref.key in defs:
            used.add(ref.key)
            ident = ref.node.this
            if ident.quoted or ident.name != ref.key:
                _retarget(ref.node, ref.key)
    return used


def _is_raw(ref: TableRef, defs: dict[str, Definition]) -> bool:
    return ref.kind is RefKind.TABLE and ref.key not in defs


def _cte(name: str, body: exp.Expression) -> exp.CTE:
    return exp.CTE(this=body, alias=exp.TableAlias(this=exp.to_identifier(name)))


def _unused(parts: list[Part], used: set[str]) -> list[Issue]:
    return [
        Issue(
            code=IssueCode.UNUSED_ALIAS,
            severity=Severity.WARNING,
            message=(
                f"Alias '{p.alias}' ({p.definition.id}) is not used by the final SELECT. "
                "Remove it or reference it."
            ),
        )
        for p in parts
        if p.alias not in used
    ]
