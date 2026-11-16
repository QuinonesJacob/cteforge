"""MCP streamable HTTP at /mcp: same bearer auth as the API, and every tool call acts as
the user whose token carried it (ADR 0018)."""

from __future__ import annotations

import builtins
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cteforge.api.app import create_app
from cteforge.domain.audit import AuditKind

BASE_URL = "http://127.0.0.1:8765"
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def _rpc(client: TestClient, token: str | None, method: str, params: dict[str, Any], id: int = 1):
    headers = dict(MCP_HEADERS)
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    body = {"jsonrpc": "2.0", "id": id, "method": method, "params": params}
    return client.post("/mcp", headers=headers, json=body)


def _result(response) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    if response.headers["content-type"].startswith("application/json"):
        return response.json()["result"]
    data = [line[5:].strip() for line in response.text.splitlines() if line.startswith("data:")]
    return json.loads(data[-1])["result"]


INIT = {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": {"name": "pytest", "version": "1"},
}


def test_mcp_requires_token(api, check_error) -> None:
    response = _rpc(api.client, None, "initialize", INIT)
    check_error(response, 401, "Unauthorized")
    assert response.headers["www-authenticate"].startswith("Bearer")
    check_error(_rpc(api.client, "wrong", "initialize", INIT), 401, "Unauthorized")


def test_mcp_initialize_and_list_tools(api) -> None:
    init = _result(_rpc(api.client, api.tokens["bot"], "initialize", INIT))
    assert init["serverInfo"]["name"] == "cteforge"
    tools = _result(_rpc(api.client, api.tokens["bot"], "tools/list", {}, id=2))
    names = {t["name"] for t in tools["tools"]}
    assert {"search", "compile_and_run", "propose_definition"} <= names
    assert not {"approve", "verify"} & names  # ADR 0007


def test_mcp_trailing_slash_reaches_endpoint(api) -> None:
    headers = MCP_HEADERS | {"Authorization": f"Bearer {api.tokens['bot']}"}
    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": INIT}
    assert api.client.post("/mcp/", headers=headers, json=body).status_code == 200


def test_mcp_tool_calls_act_as_the_token_user(api) -> None:
    assert api.propose_rr().status_code == 200
    for user in ("bot", "vic", "bot"):
        call = {"name": "search", "arguments": {"query": "realization"}}
        result = _result(_rpc(api.client, api.tokens[user], "tools/call", call))
        assert result.get("isError") is not True, result
    searches = api.ctx.audit.list_events(limit=100, kind=AuditKind.SEARCH)
    assert sorted(e.actor for e in searches) == ["bot", "bot", "vic"]


def test_mcp_permission_follows_role(api) -> None:
    """A viewer may not propose over MCP either: the tool returns a PermissionDenied error."""
    call = {
        "name": "propose_join",
        "arguments": {
            "left_table": "deals",
            "right_table": "accounts",
            "keys": [{"left": "account_id", "right": "account_id"}],
        },
    }
    result = _result(_rpc(api.client, api.tokens["vic"], "tools/call", call))
    assert result["isError"] is True
    assert "PermissionDeniedError" in json.dumps(result)
    assert "Traceback" not in json.dumps(result)


def test_app_starts_without_mcp_module(ctx, monkeypatch: pytest.MonkeyPatch, check_error) -> None:
    real_import = builtins.__import__

    def no_mcp(name, *args, **kwargs):
        if name == "cteforge.mcp.server":
            raise ImportError("not built yet")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_mcp)
    with TestClient(create_app(ctx), base_url=BASE_URL) as client:
        check_error(client.post("/mcp", json={}), 404, "NotFoundError")
        assert client.get("/api/v1/openapi.json").status_code == 200


def test_mcp_off_loopback_accepts_real_host_names(api) -> None:
    """Bound beyond loopback (auth required), the SDK's localhost-only Host check is off."""
    app = create_app(api.ctx, host="0.0.0.0")
    with TestClient(app, base_url="http://cteforge.internal:8765") as client:
        init = _result(_rpc(client, api.tokens["bot"], "initialize", INIT))
        assert init["serverInfo"]["name"] == "cteforge"
        assert _rpc(client, None, "initialize", INIT).status_code == 401


def test_mcp_on_loopback_rejects_foreign_host(api) -> None:
    """Bound to loopback, the SDK's DNS-rebinding protection stays on."""
    with TestClient(create_app(api.ctx), base_url="http://evil.example") as client:
        response = _rpc(client, api.tokens["bot"], "initialize", INIT)
        assert response.status_code == 421
