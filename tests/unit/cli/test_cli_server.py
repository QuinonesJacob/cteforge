"""serve / mcp: the CLI hands off to uvicorn / the MCP module without importing them
statically, and refuses a non-loopback bind when auth is off (ADR 0018)."""

from __future__ import annotations

import sys
import types
from collections.abc import Callable
from typing import Any

import pytest
import uvicorn
from click.testing import Result

Invoke = Callable[..., Result]


@pytest.fixture
def uvicorn_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[tuple, dict[str, Any]]]:
    calls: list[tuple[tuple, dict[str, Any]]] = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: calls.append((a, k)))
    return calls


def test_serve_runs_the_app_factory(cli: Invoke, uvicorn_calls):
    result = cli("serve", "--port", "9999", CTEFORGE_BIND_HOST="")
    assert result.exit_code == 0, result.output
    assert "http://127.0.0.1:9999" in result.output and "auth on" in result.output
    ((args, kwargs),) = uvicorn_calls
    assert args == ("cteforge.api.app:create_app",)
    assert kwargs == {"factory": True, "host": "127.0.0.1", "port": 9999, "reload": False}


def test_serve_auth_off_refuses_non_loopback(cli: Invoke, uvicorn_calls):
    for host in ("0.0.0.0", "192.168.1.5", "example.com"):
        result = cli("serve", "--host", host, CTEFORGE_AUTH="off", CTEFORGE_BIND_HOST="")
        assert result.exit_code == 2, host
        assert "CTEFORGE_AUTH=off" in result.output
    assert uvicorn_calls == []


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_serve_auth_off_allows_loopback(cli: Invoke, uvicorn_calls, host: str):
    result = cli("serve", "--host", host, CTEFORGE_AUTH="off", CTEFORGE_BIND_HOST="")
    assert result.exit_code == 0, result.output
    assert "auth OFF" in result.output
    assert uvicorn_calls[0][1]["host"] == host


def test_serve_auth_on_may_bind_anywhere(cli: Invoke, uvicorn_calls):
    result = cli("serve", "--host", "0.0.0.0", "--reload", CTEFORGE_BIND_HOST="")
    assert result.exit_code == 0, result.output
    assert uvicorn_calls[0][1]["reload"] is True


def test_serve_bad_auth_setting_exits_1(cli: Invoke, uvicorn_calls):
    result = cli("serve", CTEFORGE_AUTH="sometimes", CTEFORGE_BIND_HOST="")
    assert result.exit_code == 1
    assert uvicorn_calls == []


def test_mcp_runs_stdio_server(cli: Invoke, monkeypatch: pytest.MonkeyPatch):
    called: list[bool] = []
    fake = types.ModuleType("cteforge.mcp.server")
    fake.run_stdio = lambda: called.append(True)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "cteforge.mcp.server", fake)
    result = cli("mcp")
    assert result.exit_code == 0, result.output
    assert called == [True]
    assert result.stdout == ""  # stdout belongs to the MCP protocol.


def test_serve_never_prints_the_database_password(cli: Invoke, uvicorn_calls):
    url = "postgresql+psycopg://cteforge:s3cret-pw@db.internal:5432/library"
    result = cli("serve", CTEFORGE_DB_URL=url, CTEFORGE_BIND_HOST="")
    assert result.exit_code == 0, result.output
    assert "s3cret-pw" not in result.output
    assert "db postgresql+psycopg://cteforge:***@db.internal:5432/library" in result.output
