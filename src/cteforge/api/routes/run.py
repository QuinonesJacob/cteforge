"""Render/run, exploratory SQL and drift checks (DESIGN §8, §10, §17)."""

from __future__ import annotations

from fastapi import APIRouter

from cteforge.api.auth import Ctx, CurrentUser
from cteforge.api.schemas import CheckRequest, RevalidateRequest, RunRequest
from cteforge.services import revalidate as revalidate_service
from cteforge.services import run as run_service
from cteforge.services.cards import DefinitionDrift, RunOutcome
from cteforge.services.connections import ProfileSummary

router = APIRouter(tags=["run"])


@router.get("/profiles", response_model=list[ProfileSummary])
def list_profiles(ctx: Ctx, user: CurrentUser) -> list[ProfileSummary]:
    """Configured warehouse profiles: name and dialect only, never options or secrets."""
    return ctx.warehouses.summaries()


@router.post("/run", response_model=RunOutcome)
def run(ctx: Ctx, user: CurrentUser, body: RunRequest) -> RunOutcome:
    return run_service.compile_and_run(
        ctx,
        user,
        uses=body.uses,
        recipe_id=body.recipe_id,
        values=body.values,
        select=body.select,
        dialect=body.dialect,
        profile=body.profile,
        run=body.run,
        limit=body.limit,
        question=body.question,
    )


@router.post("/check", response_model=RunOutcome)
def check(ctx: Ctx, user: CurrentUser, body: CheckRequest) -> RunOutcome:
    return run_service.check_and_run(
        ctx,
        user,
        body.sql,
        profile=body.profile,
        run=body.run,
        limit=body.limit,
        question=body.question,
    )


@router.post("/revalidate", response_model=list[DefinitionDrift])
def revalidate(
    ctx: Ctx, user: CurrentUser, body: RevalidateRequest | None = None
) -> list[DefinitionDrift]:
    return revalidate_service.revalidate(ctx, user, body.ids if body is not None else None)
