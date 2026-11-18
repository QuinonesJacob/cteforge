"""Identity, users, tokens and team settings (ADR 0018). Tokens are returned once."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, status

from cteforge.api.auth import Ctx, CurrentUser
from cteforge.api.schemas import CreateUserRequest, SetRoleRequest, SettingsRequest
from cteforge.domain.users import User
from cteforge.services import admin
from cteforge.services.cards import TeamSettings

router = APIRouter(tags=["admin"])


@router.get("/me", response_model=User)
def me(user: CurrentUser) -> User:
    return user


@router.get("/users", response_model=list[User])
def list_users(ctx: Ctx, user: CurrentUser) -> list[User]:
    return admin.list_users(ctx, user)


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(ctx: Ctx, user: CurrentUser, body: CreateUserRequest) -> dict[str, Any]:
    """Returns {user, token}; the token is shown only in this response."""
    created, token = admin.create_user(ctx, user, body.name, body.role, body.kind)
    return {"user": created.model_dump(mode="json"), "token": token}


@router.post("/users/{name}/token")
def rotate_token(ctx: Ctx, user: CurrentUser, name: str) -> dict[str, str]:
    """Returns {name, token}; the new token is shown only in this response."""
    return {"name": name, "token": admin.rotate_token(ctx, user, name)}


@router.put("/users/{name}/role", response_model=User)
def set_role(ctx: Ctx, user: CurrentUser, name: str, body: SetRoleRequest) -> User:
    return admin.set_role(ctx, user, name, body.role)


@router.get("/settings", response_model=TeamSettings)
def get_settings(ctx: Ctx, user: CurrentUser) -> TeamSettings:
    return admin.get_settings(ctx, user)


@router.put("/settings", response_model=TeamSettings)
def set_settings(ctx: Ctx, user: CurrentUser, body: SettingsRequest) -> TeamSettings:
    """Admin only. Applies `default_connection` first (it can fail: unknown profile), then
    `mode`; each change is its own audited call."""
    settings: TeamSettings | None = None
    if "default_connection" in body.model_fields_set:
        settings = admin.set_default_connection(ctx, user, body.default_connection)
    if body.mode is not None:
        settings = admin.set_settings(ctx, user, body.mode)
    return settings if settings is not None else admin.get_settings(ctx, user)
