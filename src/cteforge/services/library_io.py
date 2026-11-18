"""Use-cases: export/import the library as YAML files (ADR 0003: git optional, not the
source of truth). File layout: files/library.py.

Export (viewer+): the latest version of every definition (codec YAML; SQL or composed,
ADR 0026) and every join edge, one file each, deterministic. `prune=True` removes other
`*.yml` files in the sub-directories (legacy `recipes/` included) so the directory mirrors
the library.

Import (contributor+) NEVER writes the library directly: every file that differs from the
store becomes a proposal and follows the normal rules (auto-apply in open mode when checks
pass and the target is not verified; otherwise review). Files equal to the store (ignoring
version, state and verification; for joins also usage_count) are skipped.
- Definitions are re-checked from scratch: the template is rendered with its original
  values into SQL, its variables become annotations (slot literal IDs), and the checks
  engine runs on the warehouse profile (`profile`, else the file's, else the default).
  A composed definition is re-checked from its parts, final SELECT and fixed values
  (`_apply.checked_composition`). State and verification in a file are ignored: only a
  person verifies (ADR 0007).
- Legacy `recipes/*.yml` files (ADR 0026) are read as composed definitions.
- Order: SQL definitions, joins, then composed definitions (recipe files included) in
  dependency order — their checks resolve parts in the store, so parts imported in the
  same run must be applied first (in strict mode, or when a part waits for review, the
  part is not found and the composed definition's proposal carries that error).
- Joins: a file whose edge is not in the map is proposed as `add`, a changed one as
  `annotate` (join type, relationship, traps, notes; the join type only from files — the
  `propose_join` use-case does not change it).
- Failure handling: every SQL definition and join proposal is built and checked (owner
  rule, profile, checks engine; joins normalized), and every composed definition's final
  SELECT is checked to be read-only, before any is stored, so an invalid file of those
  kinds (raises) leaves the store unchanged; a composed definition whose checks raise
  later leaves the earlier proposals in place. Each proposal is audited (`propose`, op
  `import_library`) right after it is submitted, so if a later step fails, what was
  already stored or auto-applied is still in the audit log. Not one database
  transaction: a store failure mid-way leaves the earlier, audited proposals in place.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cteforge.domain.access import Tool
from cteforge.domain.audit import AuditKind
from cteforge.domain.models import Definition, JoinEdge
from cteforge.domain.proposals import Proposal, ProposalKind
from cteforge.domain.users import User
from cteforge.files.library import read_library, write_library
from cteforge.services import _apply, _audit, _library
from cteforge.services.auth import require
from cteforge.services.cards import ProposalOutcome
from cteforge.services.context import AppContext

_UNVERSIONED = {"version", "state", "verified_by", "verified_at"}
_SUMMARY = "Imported from library files"


def export_library(
    ctx: AppContext, user: User, directory: str | Path, *, prune: bool = False
) -> list[str]:
    """Write the library into `directory`. Returns the written file paths."""
    fields = {"extra": {"op": "export_library"}}
    with _audit.audit_failures(ctx, user, AuditKind.ADMIN, **fields):
        require(user, Tool.GET_DEFINITION)
        written = write_library(
            directory,
            definitions=ctx.store.list_definitions(),
            joins=ctx.store.list_joins(),
            prune=prune,
        )
    _audit.record(ctx, user, AuditKind.ADMIN, extra={"op": "export_library", "files": len(written)})
    return [str(p) for p in written]


def _same(a: Definition, b: Definition) -> bool:
    return a.model_dump(exclude=_UNVERSIONED) == b.model_dump(exclude=_UNVERSIONED)


# What a join file can change: its join type and annotations (the rest is the map's own
# bookkeeping: sources, usage, who added / confirmed it, evidence).
_JOIN_CONTENT = {"join_type", "relationship", "traps", "notes"}


def _same_join(a: JoinEdge, b: JoinEdge) -> bool:
    return a.model_dump(include=_JOIN_CONTENT) == b.model_dump(include=_JOIN_CONTENT)


def _definition_proposal(
    ctx: AppContext, user: User, d: Definition, profile: str | None
) -> Proposal | None:
    current = ctx.store.get_definition(d.id)
    if current is not None and _same(current, d):
        return None
    if current is None:
        _library.check_id_available(ctx, d.id)  # not another definition's former id (ADR 0024)
    _apply.check_owner(user, d.owner, None if current is None else current.owner)
    profile_name, warehouse = _apply.warehouse_for(ctx, profile or d.profile)
    meta: dict[str, Any] = d.model_dump(exclude=_UNVERSIONED | {"composition"}) | {
        "profile": profile_name,
        "version": 1 if current is None else current.version + 1,
    }
    if d.composition is not None:
        checked, issues = _apply.checked_composition(
            ctx,
            user,
            d.composition,
            _library.original_sql(d) or None,
            d.source_dialect,
            _library.annotations_of(d),
            meta,
            warehouse,
        )
    else:
        checked, issues = _apply.checked_definition(
            _library.original_sql(d), d.source_dialect, _library.annotations_of(d), meta, warehouse
        )
    if current is None:
        kind, base, changed = ProposalKind.NEW_DEFINITION, None, True
    else:
        kind, base = ProposalKind.REVISE_DEFINITION, current.version
        changed = _apply.logic_changed(current, checked)
    return _apply.new_proposal(
        ctx,
        user,
        kind,
        d.id,
        _apply.definition_payload(checked, changed_logic=changed),
        issues,
        summary=_SUMMARY,
        base_version=base,
    )


def _submit(ctx: AppContext, user: User, proposal: Proposal) -> ProposalOutcome:
    result = _apply.submit(ctx, user, proposal)
    _apply.record_proposal(ctx, user, AuditKind.PROPOSE, result.proposal, "import_library")
    return result


def _dependency_order(composed: list[Definition]) -> list[Definition]:
    """Composed definitions so that one using another of them comes after it (a cycle is
    left in file order; its checks report it)."""
    by_id = {d.id: d for d in composed}
    ordered: list[Definition] = []
    state: dict[str, str] = {}

    def visit(d: Definition) -> None:
        if state.get(d.id):
            return
        state[d.id] = "visiting"
        assert d.composition is not None
        for ref in d.composition.uses.values():
            if ref.id in by_id and state.get(ref.id) is None:
                visit(by_id[ref.id])
        state[d.id] = "done"
        ordered.append(d)

    for d in composed:
        visit(d)
    return ordered


def import_library(
    ctx: AppContext, user: User, directory: str | Path, *, profile: str | None = None
) -> list[ProposalOutcome]:
    """Turn each changed file in `directory` into a proposal. Returns one outcome per
    proposal, in import order (SQL definitions, joins, composed definitions)."""
    fields = {"extra": {"op": "import_library"}}
    outcomes: list[ProposalOutcome] = []
    with _audit.audit_failures(ctx, user, AuditKind.PROPOSE, **fields):
        require(user, Tool.PROPOSE_DEFINITION)
        library = read_library(directory)
        plain = [d for d in library.definitions if d.composition is None]
        composed = _dependency_order(
            [d for d in [*library.definitions, *library.recipes] if d.composition is not None]
        )
        # Phase 1: build every SQL definition proposal (owner rule, profile, checks engine),
        # normalize every join and check every composed definition's final SELECT before
        # anything is stored, so a bad file leaves the library untouched.
        staged = [
            proposal
            for definition in plain
            if (proposal := _definition_proposal(ctx, user, definition, profile)) is not None
        ]
        for d in composed:
            select = _library.original_sql(d)
            if select:
                _apply.ensure_read_only(select, d.source_dialect)
        edges = [
            _library.canonical_edge(
                edge.left_table,
                edge.right_table,
                edge.keys,
                edge.join_type,
                relationship=edge.relationship,
                traps=edge.traps,
                notes=edge.notes,
            )
            for edge in library.joins
        ]
        # Phase 2: submit; each stored (or auto-applied) proposal is audited at once.
        for proposal in staged:
            outcomes.append(_submit(ctx, user, proposal))
        # Joins after definitions: applying a definition may mine the same edge, and a
        # join proposal is based on the edge as it is when proposed.
        for canonical in edges:
            existing = ctx.store.get_join(canonical.id)
            if existing is None:
                proposal = _apply.join_proposal(ctx, user, canonical, _SUMMARY, action="add")
                outcomes.append(_submit(ctx, user, proposal))
            elif not _same_join(existing, canonical):
                annotated = existing.model_copy(
                    update={k: getattr(canonical, k) for k in _JOIN_CONTENT}
                )
                proposal = _apply.join_proposal(ctx, user, annotated, _SUMMARY, action="annotate")
                outcomes.append(_submit(ctx, user, proposal))
        # Composed definitions last, parts first: their checks look up the definitions
        # submitted above.
        for definition in composed:
            proposal = _definition_proposal(ctx, user, definition, profile)
            if proposal is not None:
                outcomes.append(_submit(ctx, user, proposal))
    _audit.record(
        ctx, user, AuditKind.PROPOSE, extra={"op": "import_library", "proposals": len(outcomes)}
    )
    return outcomes
