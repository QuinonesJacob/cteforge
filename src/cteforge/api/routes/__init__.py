"""HTTP routes, one module per area (src/cteforge/api/CLAUDE.md). All under /api/v1."""

from __future__ import annotations

from fastapi import APIRouter

from cteforge.api.routes import admin, audit, definitions, proposals, run

API_PREFIX = "/api/v1"


def api_router() -> APIRouter:
    router = APIRouter(prefix=API_PREFIX)
    for module in (admin, definitions, proposals, run, audit):
        router.include_router(module.router)
    return router
