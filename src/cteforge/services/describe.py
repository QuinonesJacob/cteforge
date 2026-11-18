"""Use-case: the full card of one definition, its version history, its search document and
what references it (DESIGN §6, §15, §19; ADR 0024).

Needs Tool.GET_DEFINITION (viewer). Any state can be described, drafts included: describing
is how a person or agent learns why something is not usable. Audit is best-effort here
(`fail_closed=False`): a lookup returns no warehouse data. A former id (renamed) fails
with a NotFoundError naming the current id (`_library.definition_not_found`).
"""

from __future__ import annotations

from typing import NoReturn

from cteforge.domain.access import Tool
from cteforge.domain.audit import AuditKind
from cteforge.domain.models import DefinitionVersionInfo
from cteforge.domain.users import User
from cteforge.services import _audit, _library, _meaning
from cteforge.services.auth import require
from cteforge.services.cards import (
    DefinitionCard,
    DefinitionReferences,
    SearchDocument,
    card_from_definition,
)
from cteforge.services.context import AppContext


def _require(ctx: AppContext, user: User, what: str) -> None:
    with _audit.audit_failures(ctx, user, AuditKind.GET_DEFINITION, extra={"op": what}):
        require(user, Tool.GET_DEFINITION)


def _not_found(ctx: AppContext, user: User, id: str, version: int | None, what: str) -> NoReturn:
    _audit.record(
        ctx,
        user,
        AuditKind.GET_DEFINITION,
        fail_closed=False,
        error="NotFoundError",
        extra={"op": what, "id": id, "version": version},
    )
    raise _library.definition_not_found(ctx, id, version)


def get_definition(
    ctx: AppContext,
    user: User,
    id: str,
    version: int | None = None,
    *,
    include_source: bool = False,
    include_search_document: bool = False,
) -> DefinitionCard:
    """The definition card (latest version unless `version`), with its full history.
    `include_source` adds `source_sql` (the trusted query rebuilt from the template with
    its original values) and `annotations` (its variables, with literal IDs addressing
    that SQL), for editing it in a revision. `include_search_document` adds the generated
    Markdown text meaning search embeds (ADR 0023). Raises NotFoundError for an unknown
    id or version."""
    _require(ctx, user, "get_definition")
    definition = ctx.store.get_definition(id, version)
    if definition is None:
        _not_found(ctx, user, id, version, "get_definition")
    formers = [f.former_id for f in ctx.store.former_ids(id)]
    card = card_from_definition(definition, ctx.store.definition_history(id))
    card = card.model_copy(update={"former_ids": formers})
    if card.verified_by is not None:
        verifier = ctx.store.get_user(card.verified_by)
        if verifier is not None:
            card = card.model_copy(update={"verified_by_kind": verifier.kind})
    if include_source:
        card = card.model_copy(
            update={
                "source_sql": _library.original_sql(definition),
                "annotations": _library.annotations_of(definition),
            }
        )
    if include_search_document:
        document = _meaning.document_for(definition, formers)
        card = card.model_copy(update={"search_document": document})
    _audit.record(
        ctx,
        user,
        AuditKind.GET_DEFINITION,
        fail_closed=False,
        definitions=[f"{definition.id}@{definition.version}"],
        extra={"op": "get_definition"},
    )
    return card


def definition_history(ctx: AppContext, user: User, id: str) -> list[DefinitionVersionInfo]:
    """Every version of `id`, oldest first. Raises NotFoundError for an unknown id."""
    _require(ctx, user, "definition_history")
    history = ctx.store.definition_history(id)
    if not history:
        _not_found(ctx, user, id, None, "definition_history")
    _audit.record(
        ctx,
        user,
        AuditKind.GET_DEFINITION,
        fail_closed=False,
        definitions=[f"{h.id}@{h.version}" for h in history[-1:]],
        extra={"op": "definition_history"},
    )
    return history


def search_document(
    ctx: AppContext, user: User, id: str, version: int | None = None
) -> SearchDocument:
    """The generated Markdown search document of `id` (latest version unless `version`):
    the text meaning search embeds (ADR 0023). Raises NotFoundError."""
    _require(ctx, user, "search_document")
    definition = ctx.store.get_definition(id, version)
    if definition is None:
        _not_found(ctx, user, id, version, "search_document")
    formers = [f.former_id for f in ctx.store.former_ids(id)]
    document = SearchDocument(
        id=definition.id,
        version=definition.version,
        markdown=_meaning.document_for(definition, formers),
    )
    _audit.record(
        ctx,
        user,
        AuditKind.GET_DEFINITION,
        fail_closed=False,
        definitions=[f"{definition.id}@{definition.version}"],
        extra={"op": "search_document"},
    )
    return document


def definition_references(ctx: AppContext, user: User, id: str) -> DefinitionReferences:
    """Everything inside cteforge that uses definition `id` (ADR 0024): recipes (which
    alias, pinned or floating), pending proposals targeting or using it, and its former
    ids — what a rename updates — plus a warning about references cteforge can't see.
    Raises NotFoundError (a former id names the current one)."""
    _require(ctx, user, "definition_references")
    definition = ctx.store.get_definition(id)
    if definition is None:
        _not_found(ctx, user, id, None, "definition_references")
    references = _library.definition_references(ctx, definition)
    _audit.record(
        ctx,
        user,
        AuditKind.GET_DEFINITION,
        fail_closed=False,
        definitions=[f"{definition.id}@{definition.version}"],
        extra={"op": "definition_references"},
    )
    return references
