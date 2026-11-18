"""Identity, users, roles, personal access tokens and team settings (ADR 0018, 0027).

A token value appears in exactly one kind of response: the POST that generates it
(`/me/tokens`, `/users/{name}/tokens`, `/users` for a human caller). Every /api response
is `Cache-Control: no-store` (app.py). Who may do what is decided in services.admin.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from cteforge.api.auth import Ctx, CurrentUser
from cteforge.api.schemas import (
    CreateRoleRequest,
    CreateTokenRequest,
    CreateUserRequest,
    SettingsRequest,
    TokenToolsRequest,
    UpdateRoleRequest,
    UpdateUserRequest,
)
from cteforge.domain.users import User
from cteforge.services import admin
from cteforge.services.cards import (
    MeCard,
    NewToken,
    RoleCard,
    TeamSettings,
    TokenCard,
    ToolInfo,
    UserView,
    tool_catalog,
)

router = APIRouter(tags=["admin"])


@router.get("/me", response_model=MeCard)
def me(ctx: Ctx, user: CurrentUser) -> MeCard:
    return admin.me(ctx, user)


@router.get("/tools", response_model=list[ToolInfo])
def tools(user: CurrentUser) -> list[ToolInfo]:
    """The tool catalog (names and groups), for pickers."""
    del user
    return tool_catalog()


# ── My tokens ────────────────────────────────────────────────────────────────────────


@router.get("/me/tokens", response_model=list[TokenCard])
def my_tokens(ctx: Ctx, user: CurrentUser) -> list[TokenCard]:
    return admin.list_tokens(ctx, user)


@router.post("/me/tokens", status_code=status.HTTP_201_CREATED, response_model=NewToken)
def create_my_token(ctx: Ctx, user: CurrentUser, body: CreateTokenRequest) -> NewToken:
    """The new token is in this response only."""
    return _create_token(ctx, user, None, body)


@router.put("/me/tokens/{token_id}", response_model=TokenCard)
def set_my_token_tools(
    ctx: Ctx, user: CurrentUser, token_id: str, body: TokenToolsRequest
) -> TokenCard:
    return _set_tools(ctx, user, None, token_id, body)


@router.delete("/me/tokens/{token_id}", response_model=TokenCard)
def revoke_my_token(ctx: Ctx, user: CurrentUser, token_id: str) -> TokenCard:
    return admin.revoke_token(ctx, user, token_id)


# ── Users ────────────────────────────────────────────────────────────────────────────


@router.get("/users", response_model=list[UserView])
def list_users(ctx: Ctx, user: CurrentUser) -> list[UserView]:
    return admin.list_users(ctx, user)


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(ctx: Ctx, user: CurrentUser, body: CreateUserRequest) -> dict[str, object]:
    """Returns {user, token}: the "default" token is shown only in this response, and
    only to a human caller (an agent caller gets token null)."""
    created, token = admin.create_user(ctx, user, body.name, body.role, body.kind)
    shown = created.model_dump(mode="json", exclude={"tools", "review_all"})
    return {"user": shown, "token": token}


@router.patch("/users/{name}", response_model=UserView)
def update_user(ctx: Ctx, user: CurrentUser, name: str, body: UpdateUserRequest) -> UserView:
    return admin.update_user(ctx, user, name, role=body.role, active=body.active)


@router.get("/users/{name}/tokens", response_model=list[TokenCard])
def user_tokens(ctx: Ctx, user: CurrentUser, name: str) -> list[TokenCard]:
    return admin.list_tokens(ctx, user, name)


@router.post("/users/{name}/tokens", status_code=status.HTTP_201_CREATED, response_model=NewToken)
def create_user_token(ctx: Ctx, user: CurrentUser, name: str, body: CreateTokenRequest) -> NewToken:
    """The new token is in this response only (human callers only)."""
    return _create_token(ctx, user, name, body)


@router.put("/users/{name}/tokens/{token_id}", response_model=TokenCard)
def set_user_token_tools(
    ctx: Ctx, user: CurrentUser, name: str, token_id: str, body: TokenToolsRequest
) -> TokenCard:
    return _set_tools(ctx, user, name, token_id, body)


@router.delete("/users/{name}/tokens/{token_id}", response_model=TokenCard)
def revoke_user_token(ctx: Ctx, user: CurrentUser, name: str, token_id: str) -> TokenCard:
    return admin.revoke_token(ctx, user, token_id, name)


def _create_token(ctx: Ctx, user: User, name: str | None, body: CreateTokenRequest) -> NewToken:
    return admin.create_token(
        ctx,
        user,
        name,
        name=body.name,
        expires_at=body.expires_at,
        tool_mode=body.tool_mode,
        include=body.include,
        exclude=body.exclude,
    )


def _set_tools(
    ctx: Ctx, user: User, name: str | None, token_id: str, body: TokenToolsRequest
) -> TokenCard:
    return admin.set_token_tools(
        ctx,
        user,
        token_id,
        name,
        tool_mode=body.tool_mode,
        include=body.include,
        exclude=body.exclude,
    )


# ── Roles ────────────────────────────────────────────────────────────────────────────


@router.get("/roles", response_model=list[RoleCard])
def list_roles(ctx: Ctx, user: CurrentUser) -> list[RoleCard]:
    return admin.list_roles(ctx, user)


@router.post("/roles", status_code=status.HTTP_201_CREATED, response_model=RoleCard)
def create_role(ctx: Ctx, user: CurrentUser, body: CreateRoleRequest) -> RoleCard:
    return admin.create_role(
        ctx,
        user,
        body.name,
        description=body.description,
        allowed=body.allowed,
        defaults=body.defaults,
        review_all=body.review_all,
    )


@router.put("/roles/{name}", response_model=RoleCard)
def update_role(ctx: Ctx, user: CurrentUser, name: str, body: UpdateRoleRequest) -> RoleCard:
    return admin.update_role(
        ctx,
        user,
        name,
        description=body.description,
        allowed=body.allowed,
        defaults=body.defaults,
        review_all=body.review_all,
    )


@router.delete("/roles/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_role(ctx: Ctx, user: CurrentUser, name: str) -> Response:
    admin.delete_role(ctx, user, name)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Settings ─────────────────────────────────────────────────────────────────────────


@router.get("/settings", response_model=TeamSettings)
def get_settings(ctx: Ctx, user: CurrentUser) -> TeamSettings:
    return admin.get_settings(ctx, user)


@router.put("/settings", response_model=TeamSettings)
def set_settings(ctx: Ctx, user: CurrentUser, body: SettingsRequest) -> TeamSettings:
    """manage_settings. Applies `default_connection` first (it can fail: unknown
    profile), then `mode`; each change is its own audited call."""
    settings: TeamSettings | None = None
    if "default_connection" in body.model_fields_set:
        settings = admin.set_default_connection(ctx, user, body.default_connection)
    if body.mode is not None:
        settings = admin.set_settings(ctx, user, body.mode)
    return settings if settings is not None else admin.get_settings(ctx, user)
