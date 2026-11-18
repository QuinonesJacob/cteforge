"""The HTTP application (ADR 0018, 0019): JSON API under /api/v1, MCP (streamable HTTP) at
/mcp, and the built GUI (web/dist) at /.

- `create_app(ctx=None, *, host=None, web_dist=None)`: with no ctx, the context is opened
  from the environment (AppConfig.from_env) in the lifespan and closed on shutdown. `host`
  is the address the server binds to (default: CTEFORGE_BIND_HOST, set by `cteforge serve`,
  else 127.0.0.1); CTEFORGE_AUTH=off on a non-loopback host is refused here, at startup.
- Errors: every CteforgeError becomes {"error": {type, message, issues}} with the status
  of `ERROR_STATUS`; request validation → 422; anything unexpected → 500 "internal error"
  (logged server-side by exception class and stack frames only, never its message).
- /mcp: the MCP server from cteforge.mcp.server, behind the same bearer auth. The user is
  put in `auth.CURRENT_USER` for the request; the stateless MCP server runs each request
  in a task started from it, so its `resolve_user` (`auth.request_user`) sees that user.
- Auth off (loopback only): requests must arrive on a loopback socket (peer and local
  address, from the ASGI scope, not headers) and name a loopback Host and, when sent, a
  loopback Origin, so neither another machine nor a web page elsewhere can use the local
  admin (forged Host, DNS rebinding, CSRF).
- Security headers on every response; /api responses are never cached (tokens are shown
  once). No CORS: the GUI is served from the same origin.
"""

from __future__ import annotations

import logging
import os
import traceback
from collections.abc import AsyncIterator, Iterable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from cteforge import __version__
from cteforge.api.auth import (
    CURRENT_USER,
    WWW_AUTHENTICATE,
    Unauthorized,
    check_bind,
    is_loopback,
    request_user,
    resolve,
)
from cteforge.api.routes import API_PREFIX, api_router
from cteforge.domain.errors import (
    AnnotationError,
    AuditError,
    CodecError,
    CteforgeError,
    NotFoundError,
    PermissionDeniedError,
    PolicyError,
    RenderError,
    SqlParseError,
    StaleVersionError,
    StoreError,
    VariableValueError,
    WarehouseError,
)
from cteforge.services import search_index
from cteforge.services.context import AppConfig, AppContext, open_context

logger = logging.getLogger("cteforge.api")

DEFAULT_HOST = "127.0.0.1"
BIND_HOST_ENV = "CTEFORGE_BIND_HOST"  # Set by `cteforge serve` for the uvicorn factory.
WEB_DIST_ENV = "CTEFORGE_WEB_DIST"
MCP_PATH = "/mcp"
REPO_WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"

# First match wins (subclasses before their bases).
ERROR_STATUS: tuple[tuple[type[CteforgeError], int], ...] = (
    (PermissionDeniedError, 403),
    (NotFoundError, 404),
    (VariableValueError, 422),
    (AnnotationError, 422),
    (RenderError, 422),
    (SqlParseError, 422),
    (CodecError, 422),
    (StaleVersionError, 409),
    (PolicyError, 409),
    (StoreError, 409),  # Write conflicts (e.g. a user name that already exists).
    (WarehouseError, 502),
    (AuditError, 503),  # Fail-closed audit: the call is refused while the log is down.
)

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'self'; "
    "frame-ancestors 'none'; form-action 'self'"
)
SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "content-security-policy": CONTENT_SECURITY_POLICY,
    "x-frame-options": "DENY",
}


# ── Error bodies ─────────────────────────────────────────────────────────────────────


def error_response(
    status: int,
    type_: str,
    message: str,
    issues: Iterable[Any] = (),
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = {
        "error": {
            "type": type_,
            "message": message,
            "issues": [
                i.model_dump(mode="json") if hasattr(i, "model_dump") else i for i in issues
            ],
        }
    }
    return JSONResponse(body, status_code=status, headers=headers)


def status_for(exc: CteforgeError) -> int:
    for cls, status in ERROR_STATUS:
        if isinstance(exc, cls):
            return status
    return 500


def cteforge_error_response(exc: CteforgeError) -> JSONResponse:
    status = status_for(exc)
    if status == 500:  # An unmapped error class: do not echo its message.
        _log_unexpected(exc)
        return error_response(500, "InternalError", "internal error")
    return error_response(status, type(exc).__name__, str(exc), getattr(exc, "issues", None) or [])


def unauthorized_response(exc: Unauthorized | None = None) -> JSONResponse:
    message = str(exc) if exc is not None else str(Unauthorized())
    return error_response(
        401, "Unauthorized", message, headers={"WWW-Authenticate": WWW_AUTHENTICATE}
    )


def _log_unexpected(exc: BaseException, where: str = "") -> None:
    """Class name and stack frame locations only: neither the message nor source lines,
    which may carry connection details."""
    frames = "\n".join(
        f"  {f.filename}:{f.lineno} in {f.name}" for f in traceback.extract_tb(exc.__traceback__)
    )
    logger.error("Unhandled %s%s\n%s", type(exc).__name__, f" on {where}" if where else "", frames)


def _validation_message(exc: RequestValidationError) -> str:
    parts = []
    for error in exc.errors():
        loc = [str(p) for p in error.get("loc", ())]
        where = ".".join(loc[1:]) if len(loc) > 1 else ".".join(loc)
        parts.append(f"{where}: {error.get('msg', 'invalid')}" if where else error.get("msg", ""))
    return "; ".join(parts) or "Invalid request"


def _install_error_handlers(app: FastAPI) -> None:
    async def on_cteforge(_: Request, exc: Exception) -> Response:
        assert isinstance(exc, CteforgeError)
        return cteforge_error_response(exc)

    async def on_unauthorized(_: Request, exc: Exception) -> Response:
        assert isinstance(exc, Unauthorized)
        return unauthorized_response(exc)

    async def on_validation(_: Request, exc: Exception) -> Response:
        assert isinstance(exc, RequestValidationError)
        return error_response(422, "RequestValidationError", _validation_message(exc))

    async def on_http(_: Request, exc: Exception) -> Response:
        assert isinstance(exc, StarletteHTTPException)
        return error_response(
            exc.status_code,
            "HTTPError",
            str(exc.detail),
            headers=dict(exc.headers) if exc.headers else None,
        )

    app.add_exception_handler(CteforgeError, on_cteforge)
    app.add_exception_handler(Unauthorized, on_unauthorized)
    app.add_exception_handler(RequestValidationError, on_validation)
    app.add_exception_handler(StarletteHTTPException, on_http)


# ── ASGI middleware ──────────────────────────────────────────────────────────────────


class _SecurityHeaders:
    """Adds SECURITY_HEADERS to every HTTP response; /api responses also get no-store."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_api = scope["path"].startswith(API_PREFIX + "/") or scope["path"] == API_PREFIX

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    if name not in headers:
                        headers[name] = value
                if is_api:
                    headers["cache-control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_with_headers)


def _hostname(value: str | None, *, with_scheme: bool) -> str | None:
    if not value:
        return None
    try:
        return urlsplit(value if with_scheme else f"//{value}").hostname
    except ValueError:
        return None


def _socket_host(address: Any) -> str | None:
    """The host part of an ASGI `client` / `server` address ([host, port]), else None."""
    if isinstance(address, (list, tuple)) and address and isinstance(address[0], str):
        return address[0]
    return None


def _loopback_socket(scope: Scope) -> bool:
    """True when the TCP connection itself is local: the peer (`client`) and the address
    the server accepted it on (`server`) are both loopback. Headers are client-controlled,
    so they never stand in for this check."""
    client = _socket_host(scope.get("client"))
    server = _socket_host(scope.get("server"))
    return client is not None and is_loopback(client) and server is not None and is_loopback(server)


class _LocalOnly:
    """Auth off: only a loopback connection with a loopback Host (and Origin, when sent)
    may reach the app. The socket check refuses remote peers even when the process was
    started on a non-loopback address without `check_bind` seeing it (e.g. a bare
    `uvicorn --host 0.0.0.0`) or a client forges `Host: localhost`; the Host/Origin checks
    stop a web page on another site from acting as the local admin (DNS rebinding,
    cross-site POST). A reverse proxy on the same machine looks local: never put one in
    front of an auth-off server."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            if not _loopback_socket(scope):
                response = error_response(
                    403,
                    "PermissionDeniedError",
                    "With auth off, only connections from this machine are accepted",
                )
                await response(scope, receive, send)
                return
            headers = Headers(scope=scope)
            host = _hostname(headers.get("host"), with_scheme=False)
            if host is None or not is_loopback(host):
                response = error_response(
                    421, "MisdirectedRequest", "With auth off, use a localhost address"
                )
                await response(scope, receive, send)
                return
            origin = headers.get("origin")
            if origin is not None:
                origin_host = _hostname(origin, with_scheme=True)
                if origin_host is None or not is_loopback(origin_host):
                    response = error_response(
                        403, "PermissionDeniedError", "Cross-origin requests are not allowed"
                    )
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)


class _CatchAll:
    """Unexpected exceptions → 500 {"error": {"type": "InternalError"}} with no details."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception as exc:  # noqa: BLE001 - the last line of defense
            _log_unexpected(exc, f"{scope.get('method', '')} {scope.get('path', '')}")
            if started:
                return  # Too late for an error body; the connection just ends.
            response = error_response(500, "InternalError", "internal error")
            await response(scope, receive, send)


def _is_mcp(path: str) -> bool:
    return path == MCP_PATH or path.startswith(MCP_PATH + "/")


class _McpGateway:
    """Routes /mcp to the MCP server after bearer auth; sets CURRENT_USER for the call."""

    def __init__(self, app: ASGIApp, state: Any) -> None:
        self.app = app
        self.state = state

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _is_mcp(scope["path"]):
            await self.app(scope, receive, send)
            return
        mcp: _Mcp | None = getattr(self.state, "mcp", None)
        ctx: AppContext | None = getattr(self.state, "ctx", None)
        if mcp is None or ctx is None:
            response = error_response(404, "NotFoundError", "The MCP endpoint is not available")
            await response(scope, receive, send)
            return
        try:
            user = await run_in_threadpool(resolve, ctx, Headers(scope=scope).get("authorization"))
        except Unauthorized as exc:
            await unauthorized_response(exc)(scope, receive, send)
            return
        except CteforgeError as exc:
            await cteforge_error_response(exc)(scope, receive, send)
            return
        CURRENT_USER.set(user)
        if scope["path"] != MCP_PATH:  # "/mcp/" → the endpoint itself.
            scope = dict(scope, path=MCP_PATH, raw_path=MCP_PATH.encode())
        await mcp.app(scope, receive, send)


# ── MCP ──────────────────────────────────────────────────────────────────────────────


@dataclass
class _Mcp:
    app: ASGIApp
    server: Any  # FastMCP; its session_manager runs in the API lifespan.


def _build_mcp(ctx: AppContext, host: str) -> _Mcp | None:
    """The MCP server for this context, or None when cteforge.mcp.server is unavailable."""
    try:
        from cteforge.mcp.server import build_server, http_app
    except ImportError as exc:
        logger.warning("MCP endpoint disabled: cteforge.mcp.server unavailable (%s)", exc)
        return None
    security = None
    if not is_loopback(host):
        # The SDK default only allows localhost Host headers. Off loopback, bearer auth is
        # mandatory (check_bind), which is what DNS-rebinding protection stands in for.
        from mcp.server.transport_security import TransportSecuritySettings

        security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    server = build_server(ctx, request_user, transport_security=security)
    return _Mcp(app=http_app(server, path=MCP_PATH), server=server)


# ── Static GUI ───────────────────────────────────────────────────────────────────────


def resolve_web_dist(explicit: Path | str | None = None) -> Path | None:
    """`explicit`, else CTEFORGE_WEB_DIST, else <repo>/web/dist; None unless it holds an
    index.html."""
    raw = explicit if explicit is not None else os.environ.get(WEB_DIST_ENV, "").strip()
    path = Path(raw).expanduser() if raw else REPO_WEB_DIST
    try:
        path = path.resolve()
        return path if (path / "index.html").is_file() else None
    except (OSError, ValueError):
        return None


_GUI_HINT = {
    "cteforge": __version__,
    "api": API_PREFIX,
    "mcp": MCP_PATH,
    "gui": f"not built: run `npm run build` in web/, or set {WEB_DIST_ENV}",
}


def _install_gui(app: FastAPI, dist: Path | None) -> None:
    def static_file(rel: str) -> Path | None:
        if dist is None or not rel:
            return None
        try:
            candidate = (dist / rel).resolve()
            if candidate.is_relative_to(dist) and candidate.is_file():
                return candidate
        except (OSError, ValueError):
            return None
        return None

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def gui(full_path: str) -> Response:
        top = full_path.split("/", 1)[0]
        if top in ("api", MCP_PATH.strip("/")):
            raise HTTPException(status_code=404, detail="Not Found")
        if dist is None:
            if not full_path:
                return JSONResponse(_GUI_HINT)
            raise HTTPException(status_code=404, detail="Not Found")
        found = static_file(full_path)
        if found is not None:
            cache = "public, max-age=31536000, immutable" if top == "assets" else "no-cache"
            return FileResponse(found, headers={"cache-control": cache})
        if top == "assets":
            raise HTTPException(status_code=404, detail="Not Found")
        # SPA fallback: client-side routes (and anything unknown) get the app shell.
        return FileResponse(dist / "index.html", headers={"cache-control": "no-cache"})


# ── Factory ──────────────────────────────────────────────────────────────────────────


def create_app(
    ctx: AppContext | None = None,
    *,
    host: str | None = None,
    web_dist: Path | str | None = None,
) -> FastAPI:
    """Build the application. Raises PolicyError when auth is off and `host` is not a
    loopback address (ADR 0018)."""
    bind_host = host or os.environ.get(BIND_HOST_ENV, "").strip() or DEFAULT_HOST
    config = ctx.config if ctx is not None else AppConfig.from_env()
    check_bind(config, bind_host)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = app.state.ctx is None
        if owned:
            app.state.ctx = await run_in_threadpool(open_context, config)
            # Meaning search (ADR 0023): load the model in the background, never in a request.
            search_index.start_background(app.state.ctx)
        try:
            async with AsyncExitStack() as stack:
                mcp = _build_mcp(app.state.ctx, bind_host)
                if mcp is not None:
                    await stack.enter_async_context(mcp.server.session_manager.run())
                app.state.mcp = mcp
                yield
        finally:
            app.state.mcp = None
            if owned:
                context, app.state.ctx = app.state.ctx, None
                context.close()

    app = FastAPI(
        title="cteforge",
        version=__version__,
        lifespan=lifespan,
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=None,  # The Swagger UI loads from a CDN, which the CSP forbids.
        redoc_url=None,
    )
    app.state.ctx = ctx
    app.state.mcp = None
    _install_error_handlers(app)
    app.include_router(api_router())
    _install_gui(app, resolve_web_dist(web_dist))

    # add_middleware wraps: the last added runs first.
    app.add_middleware(_McpGateway, state=app.state)
    app.add_middleware(_CatchAll)
    if not config.auth_enabled:
        app.add_middleware(_LocalOnly)
    app.add_middleware(_SecurityHeaders)
    return app
