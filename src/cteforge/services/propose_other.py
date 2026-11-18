"""Use-case: propose a change to the join map (DESIGN §3, §18; ADR 0026).

`propose_join(action=...)`:
- `annotate` — relationship (one_to_one · one_to_many · many_to_one · many_to_many),
  traps and notes on an EXISTING edge, named by `id` or by tables + keys (NotFoundError
  otherwise). Only the fields given change; the join type and keys stay.
- `add` — an edge no definition contains: tables + keys (+ join type, relationship,
  traps, notes). An existing edge is refused (PolicyError: annotate it instead). An edge
  an agent adds never auto-applies, in any mode, and is not known (does not count for
  trust labels) until a person approves it (trust.approval).
The edge is normalized (alphabetically first table on the left, keys lowercased and
sorted; a relationship is flipped with it) and gets the deterministic id
`_library.join_edge_id`. A relationship claim is checked against the default warehouse
(`_apply.relationship_evidence`; the evidence is stored with the proposal). `sources` and
`usage_count` are never taken from the caller: mining recomputes them. Otherwise the same
auto-apply rule as definitions: open mode, no error issue, complete checks, the edge not
confirmed by a person.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import ValidationError

from cteforge.domain.access import Tool
from cteforge.domain.audit import AuditKind
from cteforge.domain.errors import CodecError, NotFoundError, PolicyError
from cteforge.domain.models import JOIN_TYPES, RELATIONSHIPS, JoinEdge, JoinKey
from cteforge.domain.users import User
from cteforge.services import _apply, _audit, _library
from cteforge.services.auth import require
from cteforge.services.cards import ProposalOutcome
from cteforge.services.context import AppContext

KeyInput = JoinKey | Mapping[str, str] | Sequence[str]


def _keys(keys: Sequence[KeyInput]) -> list[JoinKey]:
    out: list[JoinKey] = []
    for key in keys:
        if isinstance(key, JoinKey):
            out.append(key)
        elif isinstance(key, Mapping):
            try:
                out.append(JoinKey.model_validate(dict(key)))
            except ValidationError:
                raise CodecError("Each join key needs 'left' and 'right' column names") from None
        elif isinstance(key, Sequence) and not isinstance(key, str) and len(key) == 2:
            out.append(JoinKey(left=str(key[0]), right=str(key[1])))
        else:
            raise CodecError("Each join key is {left, right} or a [left, right] pair")
    if not out or any(not k.left.strip() or not k.right.strip() for k in out):
        raise CodecError("A join needs at least one key pair of non-empty column names")
    return out


JOIN_ACTIONS = ("add", "annotate")


def _clean(items: Sequence[str] | None) -> list[str] | None:
    return None if items is None else [t.strip() for t in items if t.strip()]


def _existing(
    ctx: AppContext,
    id: str | None,
    left_table: str | None,
    right_table: str | None,
    keys: Sequence[KeyInput] | None,
) -> JoinEdge:
    if id:
        edge = ctx.store.get_join(id.strip().lower())
        if edge is None:
            raise NotFoundError(f"Join '{id}' is not in the join map; list_joins shows the ids")
        return edge
    if not (left_table and right_table and keys):
        raise CodecError("annotate needs the edge's `id`, or its tables and keys")
    canonical = _library.canonical_edge(left_table, right_table, _keys(keys))
    edge = ctx.store.get_join(canonical.id)
    if edge is None:
        raise NotFoundError(
            f"No join-map edge joins '{left_table}' and '{right_table}' on those keys; use "
            "action='add' to propose a new edge"
        )
    return edge


def _oriented(relationship: str | None, given_left: str | None, edge: JoinEdge) -> str | None:
    """A relationship stated for `given_left` → ... as the stored edge's orientation."""
    if relationship is None or given_left is None:
        return relationship
    if given_left.strip().lower() == edge.left_table:
        return relationship
    return _library.flip_relationship(relationship)


def propose_join(
    ctx: AppContext,
    user: User,
    *,
    action: str = "annotate",
    id: str | None = None,
    left_table: str | None = None,
    right_table: str | None = None,
    keys: Sequence[KeyInput] | None = None,
    join_type: str | None = None,
    relationship: str | None = None,
    traps: Sequence[str] | None = None,
    notes: str | None = None,
    summary: str = "",
) -> ProposalOutcome:
    """Propose adding an edge to the join map, or annotating one (module docstring).
    A relationship is stated from `left_table` to `right_table` as given (annotate by
    `id`: in the edge's own orientation). Raises CodecError (bad input), NotFoundError
    (annotate: no such edge), PolicyError (add: the edge exists)."""
    extra = {"op": "propose_join", "action": action}
    with _audit.audit_failures(ctx, user, AuditKind.PROPOSE, extra=extra):
        require(user, Tool.PROPOSE_JOIN)
        if action not in JOIN_ACTIONS:
            raise CodecError(f"action must be one of {list(JOIN_ACTIONS)}")
        if join_type is not None and join_type.strip().lower() not in JOIN_TYPES:
            raise CodecError(f"join_type must be one of {list(JOIN_TYPES)}")
        if relationship is not None and relationship not in RELATIONSHIPS:
            raise CodecError(f"relationship must be one of {list(RELATIONSHIPS)}")
        if action == "annotate":
            current = _existing(ctx, id, left_table, right_table, keys)
            if join_type is not None and join_type.strip().lower() != current.join_type:
                raise PolicyError("annotate changes relationship, traps and notes only")
            changes: dict[str, object] = {}
            if relationship is not None:
                changes["relationship"] = _oriented(
                    relationship, left_table if not id else None, current
                )
            if traps is not None:
                changes["traps"] = _clean(traps)
            if notes is not None:
                changes["notes"] = notes.strip()
            if not changes:
                raise PolicyError("Nothing to annotate: pass relationship, traps or notes")
            edge = current.model_copy(update=changes)
        else:
            if not (left_table and left_table.strip() and right_table and right_table.strip()):
                raise CodecError("A join needs both table names")
            if keys is None:
                raise CodecError("A join needs at least one key pair")
            edge = _library.canonical_edge(
                left_table,
                right_table,
                _keys(keys),
                join_type or "inner",
                relationship=relationship,
                traps=_clean(traps) or [],
                notes=(notes or "").strip(),
            )
            if ctx.store.get_join(edge.id) is not None:
                raise PolicyError(
                    f"Join '{edge.id}' is already in the join map; use action='annotate' to "
                    "change its relationship, traps or notes"
                )
        proposal = _apply.join_proposal(ctx, user, edge, summary, action=action)
        result = _apply.submit(ctx, user, proposal)
    _apply.record_proposal(ctx, user, AuditKind.PROPOSE, result.proposal, "propose_join")
    return result
