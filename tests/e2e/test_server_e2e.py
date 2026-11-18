"""The product end to end, as a user runs it: `cteforge demo`, then `cteforge serve` as a
real process, then the built GUI, the HTTP API (with a four-eyes review), MCP over
streamable HTTP at /mcp with the agent's token, and MCP over stdio (`cteforge mcp`).

Needs `uv` and a built GUI (`npm run build` in web/); skipped otherwise.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import socket
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).resolve().parents[2]
WEB_DIST = ROOT / "web" / "dist"
UV = shutil.which("uv")
Q3_APAC = {"period": {"start": "2025-07-01", "end": "2025-10-01"}, "regions": ["APAC"]}
WON_DEALS_SQL = (
    "SELECT a.region, COUNT(*) AS won_deals FROM deals AS d "
    "JOIN accounts AS a ON a.account_id = d.account_id "
    "WHERE d.status = 'closed_won' GROUP BY a.region ORDER BY a.region"
)

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not (WEB_DIST / "index.html").is_file(), reason="web/dist not built"),
    pytest.mark.skipif(UV is None, reason="uv not on PATH"),
]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class Demo:
    directory: Path
    env: dict[str, str]  # Process env with the demo's CTEFORGE_* variables.
    admin_token: str
    agent_token: str


@dataclass
class Server:
    base: str
    demo: Demo

    def api(self, method: str, path: str, token: str, body: Any = None, **kw: Any) -> Any:
        headers = {"Authorization": f"Bearer {token}"}
        response = httpx.request(
            method, f"{self.base}/api/v1{path}", headers=headers, json=body, timeout=60, **kw
        )
        return response


def _cli(*args: str) -> list[str]:
    assert UV is not None
    return [UV, "run", "--frozen", "--project", str(ROOT), "cteforge", *args]


def _env_sh(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        match = re.fullmatch(r"export (\w+)=(.*)", line)
        if match:
            value = match.group(2)
            env[match.group(1)] = value[1:-1] if value.startswith("'") else value
    return env


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory) -> Demo:
    directory = tmp_path_factory.mktemp("e2e") / "demo"
    base_env = {k: v for k, v in os.environ.items() if not k.startswith("CTEFORGE_")}
    base_env["CTEFORGE_SEARCH_EMBEDDINGS"] = "off"  # Tests never download the model.
    done = subprocess.run(
        _cli("demo", str(directory)),
        env=base_env,
        capture_output=True,
        text=True,
        timeout=300,
        cwd=directory.parent,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    admin = re.search(r"^admin token \([^)]*\):\s+(\S+)$", done.stdout, re.M)
    agent = re.search(r"^agent token \([^)]*\):\s+(\S+)$", done.stdout, re.M)
    assert admin and agent, done.stdout
    for line in ("realization_rate: auto-applied", "booked_vs_collected: auto-applied"):
        assert line in done.stdout
    # Tokens are shown once and never written to disk.
    env_sh = (directory / "env.sh").read_text()
    assert admin.group(1) not in env_sh and agent.group(1) not in env_sh
    env = base_env | _env_sh(directory / "env.sh")
    assert env["CTEFORGE_USER"] == "admin" and env["CTEFORGE_DEFAULT_PROFILE"] == "demo"
    return Demo(directory, env, admin.group(1), agent.group(1))


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def server(demo: Demo) -> Iterator[Server]:
    port = _free_port()
    log_path = demo.directory / "serve.log"
    with log_path.open("w") as log:
        process = subprocess.Popen(
            _cli("serve", "--port", str(port)),
            env=demo.env,
            stdout=log,
            stderr=subprocess.STDOUT,
            cwd=demo.directory,
        )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 60
        while True:
            assert process.poll() is None, f"serve exited early:\n{log_path.read_text()}"
            try:
                # Up when the API answers; no token → 401 (auth is on).
                if httpx.get(f"{base}/api/v1/me", timeout=2).status_code == 401:
                    break
            except httpx.TransportError:
                pass
            assert time.monotonic() < deadline, f"serve not healthy:\n{log_path.read_text()}"
            time.sleep(0.25)
        yield Server(base, demo)
    finally:
        process.send_signal(signal.SIGINT)  # uvicorn's graceful shutdown (lifespan exit).
        try:
            code = process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            pytest.fail(f"serve did not stop on SIGINT:\n{log_path.read_text()}")
    log = log_path.read_text()
    assert code == 0, log
    assert "Application shutdown complete" in log, log
    assert "Traceback" not in log, log


# ── GUI ──────────────────────────────────────────────────────────────────────────────


def test_gui_index_and_assets_are_served(server: Server) -> None:
    index = httpx.get(f"{server.base}/", timeout=10)
    assert index.status_code == 200
    assert index.headers["content-type"].startswith("text/html")
    assert index.text == (WEB_DIST / "index.html").read_text()
    assert "default-src 'self'" in index.headers["content-security-policy"]
    assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', index.text)
    kinds = {Path(a).suffix for a in assets}
    assert {".js", ".css"} <= kinds, assets
    expected = {".js": "javascript", ".css": "text/css"}
    for asset in assets:
        response = httpx.get(f"{server.base}{asset}", timeout=10)
        assert response.status_code == 200, asset
        suffix = Path(asset).suffix
        if suffix in expected:
            assert expected[suffix] in response.headers["content-type"], (asset, response.headers)
        assert response.content == (WEB_DIST / asset.lstrip("/")).read_bytes()
    # SPA routes fall back to index.html; unknown API routes stay JSON 404s.
    deep = httpx.get(f"{server.base}/definitions/realization_rate", timeout=10)
    assert deep.status_code == 200 and deep.text == index.text
    missing = httpx.get(f"{server.base}/api/v1/nope", timeout=10)
    assert missing.status_code == 404 and missing.json()["error"]["type"] == "HTTPError"


# ── HTTP API ─────────────────────────────────────────────────────────────────────────


def test_api_library_run_and_four_eyes_review(server: Server) -> None:
    admin = server.demo.admin_token
    me = server.api("GET", "/me", admin).json()
    assert (me["name"], me["role"], me["kind"]) == ("admin", "admin", "human")

    hits = server.api("GET", "/definitions", admin, params={"q": "realization"}).json()
    assert "realization_rate" in [h["id"] for h in hits]
    card = server.api("GET", "/definitions/realization_rate", admin).json()
    assert card["state"] == "llm_annotated" and card["profile"] == "demo"
    assert {v["name"] for v in card["variables"]} >= {"period", "regions"}

    run = server.api(
        "POST",
        "/run",
        admin,
        {"uses": {"rr": {"id": "realization_rate"}}, "values": {"rr": Q3_APAC}},
    ).json()
    assert run["ran"] and run["row_count"] == 3 and run["label"] == "llm_annotated"
    assert [row[0][:7] for row in run["rows"]] == ["2025-07", "2025-08", "2025-09"]
    assert {row[1] for row in run["rows"]} == {"APAC"}

    report = server.api(
        "POST", "/parse", admin, {"sql": WON_DEALS_SQL, "dialect": "duckdb", "profile": "demo"}
    ).json()
    assert report["schema_found"] is True
    status_literal = next(lit["id"] for lit in report["literals"] if lit["value"] == "closed_won")

    # Two human approvers; the team goes strict so a new proposal waits for review.
    tokens: dict[str, str] = {}
    for name in ("ana", "ben"):
        created = server.api(
            "POST", "/users", admin, {"name": name, "role": "approver", "kind": "human"}
        )
        assert created.status_code == 201, created.text
        tokens[name] = created.json()["token"]
    ana, ben = tokens["ana"], tokens["ben"]
    assert server.api("PUT", "/settings", admin, {"mode": "strict"}).json()["mode"] == "strict"
    try:
        proposed = server.api(
            "POST",
            "/proposals/definitions",
            ana,
            {
                "sql": WON_DEALS_SQL,
                "dialect": "duckdb",
                "profile": "demo",
                "metadata": {
                    "id": "won_deals_by_region",
                    "title": "Won deals by region",
                    "meaning": "Count of closed-won deals per account region.",
                    "keywords": ["won deals", "deal count"],
                    "grain": "one row per region",
                },
                "annotations": [
                    {
                        "name": "won_status",
                        "type": "text",
                        "role": "fixed",
                        "literals": [status_literal],
                        "description": "What counts as won.",
                    }
                ],
                "summary": "Won deals per region",
            },
        )
        assert proposed.status_code == 200, proposed.text
        outcome = proposed.json()
        assert outcome["auto_applied"] is False and outcome["issues"] == []
        proposal_id = outcome["proposal"]["id"]

        pending = server.api("GET", "/proposals", admin, params={"status": "pending"}).json()
        assert [p["id"] for p in pending] == [proposal_id]
        count = server.api("GET", "/proposals/count", admin, params={"status": "pending"})
        assert count.json() == {"count": 1}

        # Four-eyes: ana may not approve her own proposal ...
        own = server.api(
            "POST", f"/proposals/{proposal_id}/approve", ana, {"verify": True, "comment": ""}
        )
        assert own.status_code == 403 and own.json()["error"]["type"] == "PermissionDeniedError"
        # ... a second human approver approves and verifies it.
        approved = server.api(
            "POST", f"/proposals/{proposal_id}/approve", ben, {"verify": True, "comment": "ok"}
        )
        assert approved.status_code == 200, approved.text
        definition = approved.json()["definition"]
        assert definition["state"] == "verified" and definition["verified_by"] == "ben"
        assert definition["owner"] == "ana"

        # Strict mode: the verified definition runs and is labeled verified.
        verified_run = server.api(
            "POST", "/run", ana, {"uses": {"w": {"id": "won_deals_by_region"}}, "values": {}}
        ).json()
        assert verified_run["label"] == "verified" and verified_run["row_count"] == 4
        assert verified_run["used"][0]["verified_by"] == "ben"
    finally:
        server.api("PUT", "/settings", admin, {"mode": "open"})

    audit = server.api("GET", "/audit", admin, params={"kind": "review", "limit": 50}).json()
    decisions = {(e["actor"], e["outcome"]) for e in audit["events"]}
    assert ("ana", "rejected") in decisions and ("ben", "ok") in decisions
    runs = server.api("GET", "/audit", admin, params={"kind": "compile_and_run"}).json()
    assert {e["actor"] for e in runs["events"]} >= {"admin", "ana"}
    # Agents can't read the audit log (approver role).
    denied = server.api("GET", "/audit", server.demo.agent_token)
    assert denied.status_code == 403


# ── MCP ──────────────────────────────────────────────────────────────────────────────


def _structured(result: Any) -> Any:
    assert not result.isError, result.content
    return result.structuredContent


@pytest.mark.anyio
async def test_mcp_over_streamable_http_with_the_agent_token(server: Server) -> None:
    headers = {"Authorization": f"Bearer {server.demo.agent_token}"}
    http = httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(30, read=300))
    async with (
        http,
        streamable_http_client(f"{server.base}/mcp", http_client=http) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        init = await session.initialize()
        assert init.serverInfo.name
        tools = (await session.list_tools()).tools
        # A contributor agent (ADR 0027): use + build tools, list/review proposals.
        assert len(tools) == 14 and "compile_and_run" in {t.name for t in tools}
        assert not {"list_users", "manage_user", "manage_role"} & {t.name for t in tools}
        hits = _structured(await session.call_tool("search", {"query": "realization rate"}))
        assert hits["result"][0]["id"].startswith("realization_rate")
        run = _structured(
            await session.call_tool(
                "compile_and_run",
                {
                    "uses": {"rr": {"id": "realization_rate"}},
                    "values": {"rr": Q3_APAC},
                    "question": "Q3 2025 APAC realization?",
                },
            )
        )
        assert run["ran"] and run["row_count"] == 3 and run["label"] == "llm_annotated"

    # A bad token never reaches MCP.
    bad = httpx.post(
        f"{server.base}/mcp",
        headers={"Authorization": "Bearer nope", "Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        timeout=10,
    )
    assert bad.status_code == 401
    # The calls were made as the agent.
    events = server.api(
        "GET", "/audit", server.demo.admin_token, params={"actor": "claude-agent"}
    ).json()["events"]
    kinds = {e["kind"] for e in events}
    assert {"search", "compile_and_run"} <= kinds
    assert {e["actor_kind"] for e in events} == {"agent"}


@pytest.mark.anyio
async def test_mcp_over_stdio_as_the_agent(demo: Demo) -> None:
    command, *args = _cli("mcp")
    params = StdioServerParameters(
        command=command,
        args=args,
        env=demo.env | {"CTEFORGE_USER": "claude-agent"},
        cwd=str(demo.directory),
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        assert len((await session.list_tools()).tools) == 14  # the role's defaults
        hits = _structured(await session.call_tool("search", {"query": "booked collected"}))
        assert hits["result"][0]["id"] == "booked_vs_collected"
        refused = _structured(
            await session.call_tool("check_and_run", {"sql": "DELETE FROM deals"})
        )
        assert refused["ran"] is False and refused["usable"] is False
        assert "not_read_only" in {issue["code"] for issue in refused["issues"]}


@pytest.mark.anyio
async def test_a_narrow_token_sees_and_runs_only_its_tools(server: Server, demo: Demo) -> None:
    """ADR 0027: a human admin mints a search-only token for the agent; MCP over HTTP and
    stdio (CTEFORGE_TOKEN) show only `search`, and the HTTP API refuses the rest too."""
    minted = server.api(
        "POST",
        "/users/claude-agent/tokens",
        server.demo.admin_token,
        {"name": "support", "tool_mode": "only", "include": ["search"]},
    )
    assert minted.status_code == 201, minted.text
    narrow = minted.json()["token"]
    assert narrow.startswith("cfg_pat_")
    denied = server.api("POST", "/check", narrow, {"sql": "SELECT 1", "run": False})
    assert denied.status_code == 403
    headers = {"Authorization": f"Bearer {narrow}"}
    http = httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(30, read=300))
    async with (
        http,
        streamable_http_client(f"{server.base}/mcp", http_client=http) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        assert [t.name for t in (await session.list_tools()).tools] == ["search"]
        hidden = await session.call_tool("check_and_run", {"sql": "SELECT 1"})
        assert hidden.isError and hidden.structuredContent["error"]["type"] == (
            "PermissionDeniedError"
        )
    command, *args = _cli("mcp")
    params = StdioServerParameters(
        command=command,
        args=args,
        env=demo.env | {"CTEFORGE_USER": "admin", "CTEFORGE_TOKEN": narrow},
        cwd=str(demo.directory),
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        assert [t.name for t in (await session.list_tools()).tools] == ["search"]
    # Revoked: the next call fails with the generic 401.
    server.api(
        "DELETE",
        f"/users/claude-agent/tokens/{minted.json()['info']['id']}",
        server.demo.admin_token,
    )
    assert server.api("GET", "/me", narrow).status_code == 401
