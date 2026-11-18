"""Use-cases: browse the join map and the tables the library reads.

DESIGN §3, §6, ADR 0026. They let an agent (or the GUI) discover what it would otherwise
learn only by trial and error: the join-map keys that keep a result's trust label, and the
columns (with types from the warehouse) of the tables the library reads. Each needs the
tool of its name (ADR 0027). Audit: AuditKind.GET_DEFINITION with `extra.op`
(`list_joins`, `describe_tables`), best effort: they return no result rows.

Join edges (ADR 0026) come with `known` (counts for trust labels: mined from a definition,
added or confirmed by a person), `confirmed` (a person approved it, or it was mined from
a verified definition), `sources` (the definitions it was mined from), relationship, traps,
notes and the last relationship evidence; `pending_proposals` only for callers holding
list_proposals. Definitions for describe_tables follow search visibility
(`_library.usable_states`). Column names and types are schema facts (the same
`parse_query` reports), never data values. Units of raw columns are not recorded by the
warehouse; `units` lists the ones the library's variables declare for a column.
"""

from __future__ import annotations

from collections.abc import Sequence

from cteforge.domain.access import Tool
from cteforge.domain.audit import AuditKind
from cteforge.domain.errors import PolicyError, WarehouseError
from cteforge.domain.models import DefinitionState
from cteforge.domain.users import User
from cteforge.services import _audit, _library
from cteforge.services.auth import require
from cteforge.services.cards import ColumnCard, JoinCard, TableCard
from cteforge.services.context import AppContext, team_mode

MAX_TABLES = 50  # describe_tables answers for at most this many tables per call.


def _require(ctx: AppContext, user: User, op: str) -> None:
    with _audit.audit_failures(ctx, user, AuditKind.GET_DEFINITION, extra={"op": op}):
        require(user, Tool(op))


def _record(ctx: AppContext, user: User, op: str, **extra: object) -> None:
    _audit.record(ctx, user, AuditKind.GET_DEFINITION, fail_closed=False, extra={"op": op, **extra})


def _states(ctx: AppContext, include_drafts: bool) -> frozenset[DefinitionState]:
    return _library.visible_states(team_mode(ctx), include_drafts)


def list_joins(
    ctx: AppContext,
    user: User,
    *,
    table: str | None = None,
    known_only: bool = False,
    definition: str | None = None,
) -> list[JoinCard]:
    """Join-map edges (DESIGN §3, ADR 0026), optionally only those touching `table` (a
    qualified name matches its unqualified form and back, as joins are matched), only
    known ones, or only those mined from `definition`. Joining on known keys keeps a
    result's label at new_logic_on_known_joins or better."""
    _require(ctx, user, "list_joins")
    edges = [
        e
        for e in ctx.store.list_joins()
        if (not known_only or e.is_known())
        and (definition is None or definition in e.sources)
        and (
            table is None
            or _library.same_table(table, e.left_table)
            or _library.same_table(table, e.right_table)
        )
    ]
    cards = _library.join_cards(ctx, user, edges)
    _record(ctx, user, "list_joins", table=table, count=len(cards))
    return cards


def _known_tables(ctx: AppContext, states: frozenset[DefinitionState]) -> list[str]:
    names: dict[str, str] = {}
    for latest in ctx.store.list_definitions():
        found = _library.newest_in_states(ctx, latest, states)
        for table in found.tables if found is not None else ():
            names.setdefault(table.lower(), table)
    for edge in ctx.store.list_joins():
        for table in (edge.left_table, edge.right_table):
            names.setdefault(table.lower(), table)
    return sorted(names.values(), key=str.lower)


def describe_tables(
    ctx: AppContext,
    user: User,
    tables: Sequence[str] | None = None,
    *,
    profile: str | None = None,
) -> list[TableCard]:
    """Tables with their columns and warehouse types, the definitions that read them, the
    join-map edges that touch them, and any units the library's variables declare for
    their columns. `tables` omitted → every table the library knows (definitions' tables
    and join-map edges), at most MAX_TABLES. Columns come from `profile` (default profile);
    with no warehouse configured, or a table the warehouse does not have, `columns` is
    empty and `found` is false. Raises PolicyError for more than MAX_TABLES names."""
    _require(ctx, user, "describe_tables")
    states = _states(ctx, False)
    names = list(dict.fromkeys(t.strip() for t in tables if t.strip())) if tables else None
    if names is not None and len(names) > MAX_TABLES:
        raise PolicyError(f"Describe at most {MAX_TABLES} tables per call ({len(names)} given)")
    names = names if names is not None else _known_tables(ctx, states)[:MAX_TABLES]
    warehouse = None
    if profile is not None or ctx.warehouses.names():
        warehouse = ctx.warehouses.get(ctx.warehouses.resolve_name(profile))
    definitions = [
        found
        for latest in ctx.store.list_definitions()
        if (found := _library.newest_in_states(ctx, latest, states)) is not None
    ]
    edges = ctx.store.list_joins()
    cards = []
    for name in names:
        columns: dict[str, str] = {}
        if warehouse is not None:
            try:
                columns = warehouse.column_types(name)
            except WarehouseError:
                columns = {}
        units: dict[str, str] = {}
        for d in definitions:
            for v in d.variables:
                if v.unit and v.column and "." in v.column:
                    table, column = v.column.rsplit(".", 1)
                    if _library.same_table(table, name):
                        units.setdefault(column.lower(), v.unit)
        cards.append(
            TableCard(
                name=name,
                found=bool(columns),
                columns=[ColumnCard(name=c, type=t) for c, t in columns.items()],
                used_by=sorted(
                    d.id for d in definitions if any(_library.same_table(name, t) for t in d.tables)
                ),
                joins=[
                    e.id
                    for e in edges
                    if _library.same_table(name, e.left_table)
                    or _library.same_table(name, e.right_table)
                ],
                units=units,
            )
        )
    _record(ctx, user, "describe_tables", tables=len(cards), profile=profile)
    return cards
