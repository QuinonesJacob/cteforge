"""Bearer-token authentication for the HTTP API and the mounted MCP endpoint (ADR 0018).

Every request resolves its acting user through services.auth: a bearer token when auth is
on, the local user (CTEFORGE_USER) when CTEFORGE_AUTH=off — which is only allowed while the
server is bound to a loopback address (`check_bind`, called by create_app at startup). The
user is also put in the `CURRENT_USER` context variable for the request, which is how the
mounted MCP server learns who is calling (`request_user`).
"""

from __future__ import annotations

import ipaddress
from contextvars import ContextVar
from typing import Annotated

from fastapi import Depends, Request
from starlette.concurrency import run_in_threadpool

from cteforge.domain.errors import PermissionDeniedError, PolicyError
from cteforge.domain.users import User
from cteforge.services.auth import authenticate, local_user
from cteforge.services.context import AppConfig, AppContext

CURRENT_USER: ContextVar[User] = ContextVar("cteforge_current_user")

WWW_AUTHENTICATE = 'Bearer realm="cteforge"'


class Unauthorized(Exception):
    """Missing or invalid bearer token (HTTP 401). The message is deliberately generic."""

    def __init__(self, message: str = "Invalid or missing token") -> None:
        super().__init__(message)


def is_loopback(host: str) -> bool:
    """127.0.0.0/8, ::1, ::ffff:127.0.0.0/104 or 'localhost' (brackets around an IPv6
    address are allowed)."""
    host = host.strip().lower()
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)  # ::ffff:127.0.0.1 (dual-stack sockets)
    return address.is_loopback or (mapped is not None and mapped.is_loopback)


def check_bind(config: AppConfig, host: str) -> None:
    """Refuse CTEFORGE_AUTH=off unless bound to loopback (ADR 0018). Raises PolicyError."""
    if not config.auth_enabled and not is_loopback(host):
        raise PolicyError(
            f"CTEFORGE_AUTH=off makes every HTTP call act as the local admin; refusing to "
            f"serve on {host!r}. Bind to 127.0.0.1 or turn auth on (ADR 0018)."
        )


def bearer_token(authorization: str | None) -> str | None:
    """The token from an `Authorization: Bearer <token>` header, else None."""
    if not authorization:
        return None
    scheme, _, value = authorization.strip().partition(" ")
    value = value.strip()
    if scheme.lower() != "bearer" or not value:
        return None
    return value


def resolve(ctx: AppContext, authorization: str | None) -> User:
    """Acting user for one HTTP call. Auth on: the token's user, else Unauthorized.
    Auth off: the local user (PermissionDeniedError if it may not be used)."""
    if not ctx.config.auth_enabled:
        return local_user(ctx)
    try:
        return authenticate(ctx, bearer_token(authorization))
    except PermissionDeniedError:
        raise Unauthorized() from None


def get_context(request: Request) -> AppContext:
    ctx = getattr(request.app.state, "ctx", None)
    if ctx is None:
        raise RuntimeError("cteforge application context is not open")
    return ctx


async def current_user(request: Request) -> User:
    """FastAPI dependency: authenticate (store lookup off the event loop) and set
    CURRENT_USER for the rest of the request."""
    ctx = get_context(request)
    user = await run_in_threadpool(resolve, ctx, request.headers.get("authorization"))
    CURRENT_USER.set(user)
    return user


def request_user() -> User:
    """`resolve_user` for the mounted MCP server: the user authenticated for this request."""
    try:
        return CURRENT_USER.get()
    except LookupError:
        raise PermissionDeniedError("No authenticated user for this call") from None


Ctx = Annotated[AppContext, Depends(get_context)]
CurrentUser = Annotated[User, Depends(current_user)]
