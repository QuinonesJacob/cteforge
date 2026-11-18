"""Use-cases: export/import the library as YAML files (ADR 0003: git optional, not the
source of truth). File layout: files/library.py.

Export (viewer+): the latest version of every definition (codec YAML), every recipe and
every join edge, one file each, deterministic. `prune=True` removes other `*.yml` files in
the three sub-directories so the directory mirrors the library.

Import (contributor+) NEVER writes the library directly: every file that differs from the
store becomes a proposal and follows the normal rules (auto-apply in open mode when checks
pass and the target is not verified; otherwise review). Files equal to the store (ignoring
version, state and verification; for joins also usage_count) are skipped.
- Definitions are re-checked from scratch: the template is rendered with its original
  values into SQL, its variables become annotations (slot literal IDs), and the checks
  engine runs on the warehouse profile (`profile`, else the file's, else the default).
  State and verification in a file are ignored: only a person verifies (ADR 0007).
- Order: definitions, joins, recipes (recipes check that their definitions exist; in strict
  mode a recipe whose new definitions are still pending fails that check).
- Failure handling: every definition and join proposal is built and checked (owner rule,
  profile, checks engine; joins normalized) before any is stored, so an invalid file (raises)
  leaves the store unchanged. Each proposal is audited (`propose`, op `import_library`)
  right after it is submitted, so if a later step fails, what was already stored or
  auto-applied is still in the audit log. Not one database transaction: a store failure
  mid-way leaves the earlier, audited proposals in place.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cteforge.domain.audit import AuditKind
from cteforge.domain.models import Definition, JoinEdge, Recipe
from cteforge.domain.proposals import Proposal, ProposalKind
from cteforge.domain.users import User
from cteforge.files.library import read_library, write_library
from cteforge.services import _apply, _audit, _library
from cteforge.services.auth import require
from cteforge.services.cards import ProposalOutcome
from cteforge.services.context import AppContext
from cteforge.trust.approval import Action

_UNVERSIONED = {"version", "state", "verified_by", "verified_at"}
_SUMMARY = "Imported from library files"


def export_library(
    ctx: AppContext, user: User, directory: str | Path, *, prune: bool = False
) -> list[str]:
    """Write the library into `directory`. Returns the written file paths."""
    fields = {"extra": {"op": "export_library"}}
    with _audit.audit_failures(ctx, user, AuditKind.ADMIN, **fields):
        require(user, Action.DESCRIBE)
        written = write_library(
            directory,
            definitions=ctx.store.list_definitions(),
            recipes=ctx.store.list_recipes(),
            joins=ctx.store.list_joins(),
            prune=prune,
        )
    _audit.record(ctx, user, AuditKind.ADMIN, extra={"op": "export_library", "files": len(written)})
    return [str(p) for p in written]


def _same(a: Definition | Recipe, b: Definition | Recipe) -> bool:
    return a.model_dump(exclude=_UNVERSIONED) == b.model_dump(exclude=_UNVERSIONED)


def _same_join(a: JoinEdge, b: JoinEdge) -> bool:
    keep = {"usage_count", "state"}
    return a.model_dump(exclude=keep) == b.model_dump(exclude=keep)


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
    meta: dict[str, Any] = d.model_dump(exclude=_UNVERSIONED) | {
        "profile": profile_name,
        "version": 1 if current is None else current.version + 1,
    }
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


def import_library(
    ctx: AppContext, user: User, directory: str | Path, *, profile: str | None = None
) -> list[ProposalOutcome]:
    """Turn each changed file in `directory` into a proposal. Returns one outcome per
    proposal, in import order (definitions, joins, recipes)."""
    fields = {"extra": {"op": "import_library"}}
    outcomes: list[ProposalOutcome] = []
    with _audit.audit_failures(ctx, user, AuditKind.PROPOSE, **fields):
        require(user, Action.PROPOSE)
        library = read_library(directory)
        # Phase 1: build every definition proposal (owner rule, profile, checks engine) and
        # normalize every join before anything is stored, so a bad file leaves the
        # library untouched.
        staged = [
            proposal
            for definition in library.definitions
            if (proposal := _definition_proposal(ctx, user, definition, profile)) is not None
        ]
        edges = [
            _library.canonical_edge(
                edge.left_table,
                edge.right_table,
                edge.keys,
                edge.join_type,
                relationship=edge.relationship,
                traps=edge.traps,
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
            if existing is None or not _same_join(existing, canonical):
                proposal = _apply.join_proposal(ctx, user, canonical, _SUMMARY)
                outcomes.append(_submit(ctx, user, proposal))
        # Recipes last: their checks look up the definitions submitted above.
        for recipe in library.recipes:
            current = ctx.store.get_recipe(recipe.id)
            if current is None or not _same(current, recipe):
                proposal = _apply.recipe_proposal(ctx, user, recipe, _SUMMARY)
                outcomes.append(_submit(ctx, user, proposal))
    _audit.record(
        ctx, user, AuditKind.PROPOSE, extra={"op": "import_library", "proposals": len(outcomes)}
    )
    return outcomes
