"""Library reads and verify: search, the Library list, card, history, parse, join map,
tables (DESIGN §3, §6, §19; ADR 0026)."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from cteforge.api.auth import Ctx, CurrentUser
from cteforge.api.schemas import ParseRequest, VerifyRequest
from cteforge.domain.models import DefinitionVersionInfo
from cteforge.services import catalog, describe, library, parse_query, review, search
from cteforge.services.cards import (
    DefinitionCard,
    DefinitionReferences,
    JoinCard,
    LibraryPage,
    ParseReport,
    SearchDocument,
    SearchHit,
    TableCard,
)

router = APIRouter(tags=["definitions"])


@router.get("/definitions", response_model=list[SearchHit])
def search_definitions(
    ctx: Ctx,
    user: CurrentUser,
    q: str = "",
    include_drafts: bool = False,
    limit: int = 10,
    question: str | None = None,
) -> list[SearchHit]:
    return search.search(
        ctx, user, q, limit=limit, include_drafts=include_drafts, question=question
    )


@router.get("/definitions/{id}", response_model=DefinitionCard)
def get_definition(
    ctx: Ctx,
    user: CurrentUser,
    id: str,
    version: int | None = None,
    include_source: bool = False,
) -> DefinitionCard:
    return describe.get_definition(ctx, user, id, version, include_source=include_source)


@router.get("/definitions/{id}/search-document", response_model=SearchDocument)
def search_document(
    ctx: Ctx, user: CurrentUser, id: str, version: int | None = None
) -> SearchDocument:
    return describe.search_document(ctx, user, id, version)


@router.get("/definitions/{id}/references", response_model=DefinitionReferences)
def definition_references(ctx: Ctx, user: CurrentUser, id: str) -> DefinitionReferences:
    return describe.definition_references(ctx, user, id)


@router.get("/definitions/{id}/history", response_model=list[DefinitionVersionInfo])
def definition_history(ctx: Ctx, user: CurrentUser, id: str) -> list[DefinitionVersionInfo]:
    return describe.definition_history(ctx, user, id)


@router.post("/definitions/{id}/verify", response_model=DefinitionCard)
def verify_definition(ctx: Ctx, user: CurrentUser, id: str, body: VerifyRequest) -> DefinitionCard:
    return review.verify_definition(ctx, user, id, body.version)


@router.post("/parse", response_model=ParseReport)
def parse(ctx: Ctx, user: CurrentUser, body: ParseRequest) -> ParseReport:
    return parse_query.parse_query(ctx, user, body.sql, body.dialect, body.profile)


@router.get("/joins", response_model=list[JoinCard])
def list_joins(
    ctx: Ctx,
    user: CurrentUser,
    table: str | None = None,
    known_only: bool = False,
    definition: str | None = None,
) -> list[JoinCard]:
    return catalog.list_joins(ctx, user, table=table, known_only=known_only, definition=definition)


@router.get("/tables", response_model=list[TableCard])
def describe_tables(
    ctx: Ctx,
    user: CurrentUser,
    name: Annotated[list[str] | None, Query()] = None,
    profile: str | None = None,
) -> list[TableCard]:
    return catalog.describe_tables(ctx, user, name, profile=profile)


@router.get("/library", response_model=LibraryPage)
def list_library(
    ctx: Ctx,
    user: CurrentUser,
    q: str = "",
    trust: Annotated[list[str] | None, Query()] = None,
    approval: Annotated[list[str] | None, Query()] = None,
    created_by: Annotated[list[str] | None, Query()] = None,
    verified_by: Annotated[list[str] | None, Query()] = None,
    needs_my_review: bool = False,
    sort: Literal["relevance", "oldest"] = "relevance",
) -> LibraryPage:
    """The Library (ADR 0026): definitions + the proposals touching them, filtered;
    sorted by relevance then trust, or by the oldest pending proposal (ADR 0029)."""
    return library.list_library(
        ctx,
        user,
        q=q,
        trust=trust,
        approval=approval,
        created_by=created_by,
        verified_by=verified_by,
        needs_my_review=needs_my_review,
        sort=sort,
    )
