"""`serve` (HTTP API + MCP at /mcp + GUI, ADR 0019) and `mcp` (stdio MCP, ADR 0018).

The entry-point contract lets cli import only services and domain, so the API and MCP
packages are loaded by name at call time: uvicorn imports the app factory
"cteforge.api.app:create_app" itself, and `mcp` uses importlib."""

from __future__ import annotations

import importlib
import ipaddress
import os
from typing import Annotated

import typer

from cteforge.cli._common import app, errors, usage
from cteforge.services.context import AppConfig

APP_FACTORY = "cteforge.api.app:create_app"
MCP_MODULE = "cteforge.mcp.server"
BIND_HOST_ENV = "CTEFORGE_BIND_HOST"  # The host `serve` binds to, for the API's checks.


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


@app.command()
def serve(
    host: Annotated[str, typer.Option("--host", help="Bind address")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="Port")] = 8765,
    reload: Annotated[bool, typer.Option("--reload/--no-reload", help="Dev reload")] = False,
) -> None:
    """Run the HTTP API, the MCP endpoint (/mcp) and the GUI (/) in one process."""
    with errors():
        config = AppConfig.from_env()
    if not config.auth_enabled and not _is_loopback(host):
        usage(
            f"CTEFORGE_AUTH=off makes every HTTP call act as the local admin; refusing to "
            f"bind to {host!r}. Bind to 127.0.0.1 or turn auth on (ADR 0018)."
        )
    import uvicorn  # Imported here: other commands should not pay for the server stack.

    os.environ[BIND_HOST_ENV] = host
    auth = "on (bearer tokens)" if config.auth_enabled else f"OFF (acting as '{config.user}')"
    typer.echo(f"cteforge serving on http://{host}:{port}  (auth {auth}; db {config.db_url})")
    uvicorn.run(APP_FACTORY, factory=True, host=host, port=port, reload=reload)


@app.command()
def mcp() -> None:
    """Run the MCP server on stdio, acting as CTEFORGE_USER (for Claude Code and others)."""
    server = importlib.import_module(MCP_MODULE)
    with errors():
        server.run_stdio()
