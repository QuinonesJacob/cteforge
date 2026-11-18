"""MCP server (ADR 0018, DESIGN §6) through the SDK's in-memory client session.

A tmp SQLite library + a DuckDB profile on a tmp copy of the demo warehouse; the example
definition is proposed through services (auto-applied as llm_annotated in open mode).
"""

from __future__ import annotations

import contextvars
import json
import os
import shutil
import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from mcp.client.session import ClientSession
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import CallToolResult
from starlette.applications import Starlette
from starlette.routing import Mount

from cteforge.domain.audit import AuditKind
from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import PermissionDeniedError
from cteforge.domain.issues import IssueCode
from cteforge.domain.models import DefinitionState, TrustLabel
from cteforge.domain.users import ActorKind, User, UserRole
from cteforge.mcp import descriptions
from cteforge.mcp.server import TOOL_NAMES, build_server, current_request, http_app
from cteforge.services.context import AppConfig, AppContext, open_context
from cteforge.services.propose import propose_definition

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"

ANALYST = User(name="analyst", role=UserRole.CONTRIBUTOR)
AGENT = User(name="claude-agent", role=UserRole.CONTRIBUTOR, kind=ActorKind.AGENT)
AGENT_APPROVER = User(name="robo", role=UserRole.APPROVER, kind=ActorKind.AGENT)
VIEWER_AGENT = User(name="reader-agent", role=UserRole.VIEWER, kind=ActorKind.AGENT)

Q3_APAC = {"period": {"start": "2025-07-01", "end": "2025-10-01"}, "regions": ["APAC"]}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="module")
def warehouse_copy(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("mcp_wh") / "warehouse.duckdb"
    shutil.copy(EXAMPLES / "warehouse.duckdb", path)
    return path


def _example(name: str) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    sql = (EXAMPLES / "queries" / f"{name}.sql").read_text()
    raw = yaml.safe_load((EXAMPLES / "definitions" / f"{name}.annotations.yml").read_text())
    annotations = raw.pop("variables")
    return sql, raw, annotations


@pytest.fixture
def ctx(tmp_path: Path, warehouse_copy: Path) -> Iterator[AppContext]:
    profile = WarehouseProfile(
        name="demo", dialect=Dialect.DUCKDB, options={"path": str(warehouse_copy)}
    )
    config = AppConfig(db_url=f"sqlite:///{tmp_path / 'lib.db'}")
    with open_context(config, profiles=[profile]) as context:
        sql, metadata, annotations = _example("realization_rate")
        outcome = propose_definition(
            context,
            ANALYST,
            sql=sql,
            dialect=Dialect.DUCKDB,
            metadata=metadata,
            annotations=annotations,
        )
        assert outcome.auto_applied
        yield context


@asynccontextmanager
async def client(ctx: AppContext, user: User) -> AsyncIterator[ClientSession]:
    server = build_server(ctx, lambda: user)
    async with create_connected_server_and_client_session(server) as session:
        yield session


def text_of(result: CallToolResult) -> str:
    return "\n".join(block.text for block in result.content if block.type == "text")


def ok(result: CallToolResult) -> dict[str, Any]:
    assert not result.isError, text_of(result)
    assert result.structuredContent is not None
    return result.structuredContent


def error(result: CallToolResult) -> tuple[str, dict[str, Any]]:
    assert result.isError
    body = text_of(result)
    assert "Traceback" not in body
    assert result.structuredContent is not None
    return body, result.structuredContent["error"]


# ── Tools and schemas ────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_lists_the_tools_with_sane_schemas(ctx: AppContext) -> None:
    async with client(ctx, AGENT) as session:
        tools = (await session.list_tools()).tools
    by_name = {t.name: t for t in tools}
    assert sorted(by_name) == sorted(TOOL_NAMES) and len(by_name) == 12
    assert not [n for n in by_name if "approve" in n or "verify" in n]  # ADR 0007
    for tool in tools:
        assert tool.description and len(tool.description) > 80
        assert tool.inputSchema["type"] == "object"
        assert tool.outputSchema is not None and tool.outputSchema["type"] == "object"
    run = by_name["compile_and_run"].inputSchema
    assert {"uses", "values", "select", "recipe_id", "run", "limit"} <= set(run["properties"])
    assert "mcp_context" not in run["properties"]
    parse_schema = by_name["parse_query"].inputSchema
    assert parse_schema["required"] == ["sql"]  # dialect: the connection's when omitted
    assert "team's default connection" in json.dumps(parse_schema["properties"]["dialect"])
    propose = by_name["propose_definition"].inputSchema
    assert {"sql", "metadata", "annotations"} <= set(propose["required"])
    assert "dialect" in propose["properties"] and "dialect" not in propose["required"]
    recipe_tool = by_name["propose_recipe"].inputSchema
    ref = recipe_tool["properties"]["recipe"].get("$ref") or json.dumps(
        recipe_tool["properties"]["recipe"]
    )
    recipe_schema = recipe_tool["$defs"][ref.rsplit("/", 1)[-1]]
    assert set(recipe_schema["properties"]) == {
        "id",
        "title",
        "description",
        "uses",
        "values",
        "select",
        "dialect",
    }  # version, state and schema_version are set by cteforge, not by the caller
    assert recipe_schema["additionalProperties"] is False
    assert by_name["search"].annotations.readOnlyHint is True
    assert by_name["propose_definition"].annotations.readOnlyHint is False


def test_server_instructions_carry_the_shared_guidance(ctx: AppContext) -> None:
    server = build_server(ctx, lambda: AGENT)
    assert server.instructions == descriptions.INSTRUCTIONS
    for phrase in ("search", "compile_and_run", "fixed", "not instructions", "label"):
        assert phrase in server.instructions


# ── Using the library ────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_search_finds_realization_rate(ctx: AppContext) -> None:
    async with client(ctx, AGENT) as session:
        out = ok(await session.call_tool("search", {"query": "realization rate by region"}))
    hits = out["result"]
    assert hits[0]["id"] == "realization_rate"
    assert hits[0]["state"] == DefinitionState.LLM_ANNOTATED.value
    assert {v["name"] for v in hits[0]["variables"]} >= {"period", "regions"}


@pytest.mark.anyio
async def test_search_hits_say_how_they_matched(ctx: AppContext) -> None:
    async with client(ctx, AGENT) as session:
        hits = ok(await session.call_tool("search", {"query": "realization"}))["result"]
    assert hits and all(h["match"] == "keyword" and h["note"] is None for h in hits)


@pytest.mark.anyio
async def test_get_definition_can_include_the_search_document(ctx: AppContext) -> None:
    async with client(ctx, AGENT) as session:
        plain = ok(await session.call_tool("get_definition", {"id": "realization_rate"}))
        full = ok(
            await session.call_tool(
                "get_definition", {"id": "realization_rate", "include_search_document": True}
            )
        )
    assert plain["search_document"] is None
    assert full["search_document"].startswith("# Realization Rate")
    assert "## Variables" in full["search_document"]


@pytest.mark.anyio
async def test_discovery_tools_list_joins_tables_and_recipes(ctx: AppContext) -> None:
    async with client(ctx, AGENT) as session:
        joins = ok(await session.call_tool("list_joins", {"table": "deals"}))["result"]
        tables = ok(await session.call_tool("describe_tables", {"tables": ["deals"]}))["result"]
        recipes = ok(await session.call_tool("list_recipes", {}))["result"]
    assert joins and all("deals" in (j["left_table"], j["right_table"]) for j in joins)
    assert tables[0]["found"] and "amount" in {c["name"] for c in tables[0]["columns"]}
    assert recipes == []


@pytest.mark.anyio
async def test_get_definition_returns_variables_with_input_formats(ctx: AppContext) -> None:
    async with client(ctx, AGENT) as session:
        card = ok(await session.call_tool("get_definition", {"id": "realization_rate"}))
    variables = {v["name"]: v for v in card["variables"]}
    assert variables["period"]["input_format"] == '{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}'
    assert variables["period"]["role"] == "changeable"
    assert variables["period"]["shape"] == "half_open"
    assert variables["won_statuses"]["role"] == "fixed"
    assert variables["regions"]["allowed"] == ["EMEA", "NA", "APAC", "LATAM"]
    assert card["version"] == 1 and len(card["history"]) == 1


@pytest.mark.anyio
async def test_compile_and_run_q3_2025_apac(ctx: AppContext) -> None:
    args = {"uses": {"rr": {"id": "realization_rate"}}, "values": {"rr": Q3_APAC}}
    async with client(ctx, AGENT) as session:
        out = ok(await session.call_tool("compile_and_run", args))
    assert out["usable"] and out["ran"]
    assert out["label"] == TrustLabel.LLM_ANNOTATED.value
    assert out["columns"] == ["month", "region", "realization_rate"]
    assert out["row_count"] == len(out["rows"]) == 3
    assert {row[1] for row in out["rows"]} == {"APAC"}
    assert {row[0][:7] for row in out["rows"]} == {"2025-07", "2025-08", "2025-09"}
    assert out["used"][0]["id"] == "realization_rate"
    event = ctx.audit.list_events(limit=1)[0]
    assert event.kind is AuditKind.COMPILE_AND_RUN and event.actor == AGENT.name


@pytest.mark.anyio
async def test_bad_value_is_a_tool_error_with_the_error_type(ctx: AppContext) -> None:
    args = {"uses": {"rr": {"id": "realization_rate"}}, "values": {"rr": {"period": "2025-Q3"}}}
    async with client(ctx, AGENT) as session:
        body, err = error(await session.call_tool("compile_and_run", args))
    assert body.startswith("VariableValueError: ")
    assert err["type"] == "VariableValueError" and err["message"] in body
    assert "period" in body


@pytest.mark.anyio
async def test_check_and_run_refuses_delete(ctx: AppContext) -> None:
    async with client(ctx, AGENT) as session:
        out = ok(await session.call_tool("check_and_run", {"sql": "DELETE FROM deals"}))
    assert not out["usable"] and not out["ran"] and out["rows"] == []
    assert IssueCode.NOT_READ_ONLY.value in {i["code"] for i in out["issues"]}
    assert ctx.warehouses.get("demo").run("SELECT COUNT(*) FROM deals", 1).rows[0][0] > 0


@pytest.mark.anyio
async def test_check_and_run_labels_exploratory_sql(ctx: AppContext) -> None:
    sql = "SELECT region, COUNT(*) AS n FROM accounts GROUP BY region"
    async with client(ctx, AGENT) as session:
        out = ok(await session.call_tool("check_and_run", {"sql": sql, "limit": 2}))
    assert out["ran"] and out["row_count"] <= 2
    assert out["label"] in {
        TrustLabel.NEW_LOGIC_KNOWN_JOINS.value,
        TrustLabel.EXPLORATORY.value,
    }


# ── Building the library ─────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_parse_then_propose_definition_auto_applies_in_open_mode(ctx: AppContext) -> None:
    sql, metadata, annotations = _example("booked_vs_collected")
    async with client(ctx, AGENT) as session:
        report = ok(await session.call_tool("parse_query", {"sql": sql, "dialect": "duckdb"}))
        ids = {lit["id"] for lit in report["literals"]}
        assert {"booked.L1", "collected.L1", "main.L1"} <= ids
        assert report["schema_found"]
        no_dialect = ok(await session.call_tool("parse_query", {"sql": sql}))
        assert no_dialect["dialect"] == "duckdb"  # the default connection's
        out = ok(
            await session.call_tool(
                "propose_definition",
                {
                    "sql": sql,
                    "dialect": "duckdb",
                    "metadata": metadata,
                    "annotations": annotations,
                    "summary": "Booked vs collected per region",
                },
            )
        )
        assert out["auto_applied"] is True
        assert out["definition"]["state"] == DefinitionState.LLM_ANNOTATED.value
        assert out["proposal"]["author"] == AGENT.name
        hits = ok(await session.call_tool("search", {"query": "booked collected"}))["result"]
    assert hits[0]["id"] == "booked_vs_collected"


@pytest.mark.anyio
async def test_revise_on_a_stale_base_returns_the_issue(ctx: AppContext) -> None:
    args = {"id": "realization_rate", "base_version": 7, "metadata": {"keywords": ["cash"]}}
    async with client(ctx, AGENT) as session:
        body, err = error(await session.call_tool("revise_definition", args))
    assert body.startswith("StaleVersionError: ")
    assert [i["code"] for i in err["issues"]] == [IssueCode.STALE_BASE_VERSION.value]
    issues_line = body.split("\nissues: ", 1)[1]
    assert json.loads(issues_line)[0]["code"] == IssueCode.STALE_BASE_VERSION.value


@pytest.mark.anyio
async def test_revise_metadata_only_applies(ctx: AppContext) -> None:
    args = {"id": "realization_rate", "base_version": 1, "metadata": {"keywords": ["cash"]}}
    async with client(ctx, AGENT) as session:
        out = ok(await session.call_tool("revise_definition", args))
    assert out["auto_applied"] and out["definition"]["version"] == 2
    assert out["definition"]["keywords"] == ["cash"]
    assert out["definition"]["title"].startswith("Realization Rate")  # kept


@pytest.mark.anyio
async def test_revise_with_new_id_renames_and_the_old_id_names_the_new_one(
    ctx: AppContext,
) -> None:
    # ADR 0024: an agent proposes a rename through revise_definition(new_id=...); a later
    # lookup of the old id is a NotFoundError naming the new one.
    args = {"id": "realization_rate", "base_version": 1, "new_id": "realization_rate_monthly"}
    async with client(ctx, AGENT) as session:
        out = ok(await session.call_tool("revise_definition", args))
        body, err = error(await session.call_tool("get_definition", {"id": "realization_rate"}))
    assert out["auto_applied"] and out["proposal"]["kind"] == "rename_definition"
    assert out["definition"]["id"] == "realization_rate_monthly"
    assert body.startswith("NotFoundError: ")
    assert "was renamed to 'realization_rate_monthly'" in err["message"]


@pytest.mark.anyio
async def test_propose_join_and_recipe(ctx: AppContext) -> None:
    recipe = {
        "id": "apac_q3",
        "title": "APAC Q3 2025 realization",
        "uses": {"rr": {"id": "realization_rate"}},
        "values": {"rr": Q3_APAC},
        "dialect": "duckdb",
    }
    join = {
        "left_table": "deals",
        "right_table": "invoices",
        "keys": [{"left": "deal_id", "right": "deal_id"}],
        "relationship": "one_to_many",
        "traps": ["fan-out: one deal has many invoices"],
    }
    async with client(ctx, AGENT) as session:
        joined = ok(await session.call_tool("propose_join", join))
        saved = ok(await session.call_tool("propose_recipe", {"recipe": recipe}))
        rerun = ok(await session.call_tool("compile_and_run", {"recipe_id": "apac_q3"}))
    assert joined["proposal"]["kind"] == "new_join" and joined["auto_applied"]
    assert saved["proposal"]["kind"] == "new_recipe" and saved["auto_applied"]
    assert rerun["row_count"] == 3


# ── Permissions and errors ───────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_viewer_cannot_propose_and_the_error_is_clean(ctx: AppContext) -> None:
    sql, metadata, annotations = _example("booked_vs_collected")
    args = {"sql": sql, "dialect": "duckdb", "metadata": metadata, "annotations": annotations}
    async with client(ctx, VIEWER_AGENT) as session:
        body, err = error(await session.call_tool("propose_definition", args))
    assert body.startswith("PermissionDeniedError: ")
    assert err == {"type": "PermissionDeniedError", "message": err["message"], "issues": []}
    assert VIEWER_AGENT.name in err["message"]
    assert ctx.store.get_definition("booked_vs_collected") is None


@pytest.mark.anyio
async def test_an_agent_approver_has_no_way_to_verify(ctx: AppContext) -> None:
    async with client(ctx, AGENT_APPROVER) as session:
        names = {t.name for t in (await session.list_tools()).tools}
        result = await session.call_tool("verify_definition", {"id": "realization_rate"})
    assert names == set(TOOL_NAMES)
    assert result.isError and "Unknown tool" in text_of(result)
    assert ctx.store.get_definition("realization_rate").state is DefinitionState.LLM_ANNOTATED


@pytest.mark.anyio
async def test_permission_error_from_resolve_user_surfaces(ctx: AppContext) -> None:
    def refuse() -> User:
        raise PermissionDeniedError("Invalid or missing token")

    server = build_server(ctx, refuse)
    async with create_connected_server_and_client_session(server) as session:
        body, err = error(await session.call_tool("search", {"query": "realization"}))
    assert body == "PermissionDeniedError: Invalid or missing token"
    assert err["type"] == "PermissionDeniedError"


@pytest.mark.anyio
async def test_invalid_arguments_and_internal_errors_hide_details(ctx: AppContext) -> None:
    def broken() -> User:
        raise RuntimeError("postgresql://admin:hunter2@db/prod")

    async with client(ctx, AGENT) as session:
        body, err = error(await session.call_tool("search", {"query": "x", "limit": 0}))
    assert body.startswith("InvalidArguments: limit:") and err["type"] == "InvalidArguments"
    server = build_server(ctx, broken)
    async with create_connected_server_and_client_session(server) as session:
        body, err = error(await session.call_tool("search", {"query": "x"}))
    assert body.startswith("InternalError: ") and "hunter2" not in body
    assert err["type"] == "InternalError"


@pytest.mark.anyio
async def test_unknown_definition_is_not_found(ctx: AppContext) -> None:
    async with client(ctx, AGENT) as session:
        body, err = error(await session.call_tool("get_definition", {"id": "nope"}))
    assert body.startswith("NotFoundError: ") and err["type"] == "NotFoundError"


@pytest.mark.anyio
async def test_user_is_resolved_on_every_call(ctx: AppContext) -> None:
    users = iter([AGENT, VIEWER_AGENT])
    server = build_server(ctx, lambda: next(users))
    async with create_connected_server_and_client_session(server) as session:
        await session.call_tool("search", {"query": "realization"})
        await session.call_tool("search", {"query": "realization"})
    actors = [e.actor for e in ctx.audit.list_events(limit=2)]
    assert sorted(actors) == sorted([AGENT.name, VIEWER_AGENT.name])


# ── Streamable HTTP ──────────────────────────────────────────────────────────────────


def _sse_json(response: httpx.Response) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    data = [line[5:].strip() for line in response.text.splitlines() if line.startswith("data:")]
    return json.loads(data[-1])


@pytest.mark.anyio
async def test_http_app_mounts_and_resolves_the_user_per_request(ctx: AppContext) -> None:
    acting: contextvars.ContextVar[User] = contextvars.ContextVar("acting")
    by_header = {"agent": AGENT, "viewer": VIEWER_AGENT}

    def resolve() -> User:
        request = current_request()
        assert request is not None
        assert acting.get() is by_header[request.headers["x-user"]]  # the API's contextvar
        return by_header[request.headers["x-user"]]

    server: FastMCP = build_server(ctx, resolve)
    mcp_app = http_app(server)

    async def with_user(scope, receive, send):  # what the API's auth would do
        if scope["type"] == "http":
            user = dict(scope["headers"]).get(b"x-user", b"").decode()
            acting.set(by_header[user])
        await mcp_app(scope, receive, send)

    app = Starlette(routes=[Mount("/mcp", app=with_user)])
    headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
    }
    call = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "search", "arguments": {"query": "realization"}},
    }
    transport = httpx.ASGITransport(app=app)
    async with server.session_manager.run():
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8000") as http:
            for name in ("agent", "viewer"):
                response = await http.post("/mcp/", json=call, headers=headers | {"x-user": name})
                result = _sse_json(response)["result"]
                assert not result["isError"], result
                assert result["structuredContent"]["result"][0]["id"] == "realization_rate"
    actors = [e.actor for e in ctx.audit.list_events(limit=2)]
    assert sorted(actors) == sorted([AGENT.name, VIEWER_AGENT.name])


# ── stdio ────────────────────────────────────────────────────────────────────────────


def _stdio_params(db_url: str, user: str) -> Any:
    from mcp.client.stdio import StdioServerParameters

    env = dict(os.environ) | {"CTEFORGE_DB_URL": db_url, "CTEFORGE_USER": user}
    env.pop("CTEFORGE_CONNECTIONS", None)
    return StdioServerParameters(
        command=sys.executable,
        args=["-c", "from cteforge.mcp.server import run_stdio; run_stdio()"],
        env=env,
    )


@pytest.mark.anyio
async def test_run_stdio_serves_as_the_local_user(tmp_path: Path) -> None:
    from mcp.client.stdio import stdio_client

    from cteforge.services import admin

    db_url = f"sqlite:///{tmp_path / 'stdio.db'}"
    with open_context(AppConfig(db_url=db_url)) as context:
        root, _ = admin.bootstrap_admin(context, "root")
        admin.create_user(context, root, "jo", UserRole.CONTRIBUTOR, ActorKind.AGENT)
    params = _stdio_params(db_url, "jo")
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        assert init.instructions == descriptions.INSTRUCTIONS
        assert len((await session.list_tools()).tools) == len(TOOL_NAMES)
        hits = await session.call_tool("search", {"query": "realization"})
    assert ok(hits)["result"] == []
    with open_context(AppConfig(db_url=db_url)) as context:
        jo = context.store.get_user("jo")
        assert jo.role is UserRole.CONTRIBUTOR and jo.kind is ActorKind.AGENT  # as stored
        assert context.audit.list_events(limit=1)[0].actor == "jo"


def test_run_stdio_never_creates_the_local_user(tmp_path: Path, monkeypatch) -> None:
    """An agent's MCP client on a fresh store must not become its first human admin."""
    from cteforge.mcp.server import run_stdio

    db_url = f"sqlite:///{tmp_path / 'fresh.db'}"
    monkeypatch.setenv("CTEFORGE_DB_URL", db_url)
    monkeypatch.setenv("CTEFORGE_USER", "claude-agent")
    monkeypatch.delenv("CTEFORGE_CONNECTIONS", raising=False)
    monkeypatch.delenv("CTEFORGE_AUTH", raising=False)
    with pytest.raises(PermissionDeniedError, match="user add"):
        run_stdio()
    with open_context(AppConfig(db_url=db_url)) as context:
        assert context.store.list_users() == []
