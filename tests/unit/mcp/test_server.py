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

from cteforge.domain.access import Tool
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
ADMIN_AGENT = User(name="adminbot", role=UserRole.ADMIN, kind=ActorKind.AGENT)

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
    assert len(TOOL_NAMES) == 15  # ADR 0026: no propose_recipe / list_recipes
    assert not {"propose_recipe", "list_recipes"} & set(TOOL_NAMES)
    # A contributor agent: use + build tools, list_proposals and review_proposal (owners
    # approve what they own); no admin tools (ADR 0027).
    assert set(by_name) == set(TOOL_NAMES) - {"list_users", "manage_user", "manage_role"}
    for tool in tools:
        assert tool.description and len(tool.description) > 80
        assert tool.inputSchema["type"] == "object"
        assert tool.outputSchema is not None and tool.outputSchema["type"] == "object"
    run = by_name["compile_and_run"].inputSchema
    assert {"uses", "values", "select", "run", "limit"} <= set(run["properties"])
    assert "recipe_id" not in run["properties"]
    assert "mcp_context" not in run["properties"]
    parse_schema = by_name["parse_query"].inputSchema
    assert parse_schema["required"] == ["sql"]  # dialect: the connection's when omitted
    assert "team's default connection" in json.dumps(parse_schema["properties"]["dialect"])
    propose = by_name["propose_definition"].inputSchema
    assert propose["required"] == ["metadata"]  # sql OR composition; annotations optional
    assert {"sql", "composition", "annotations", "dialect"} <= set(propose["properties"])
    assert "composition" in by_name["revise_definition"].inputSchema["properties"]
    join_schema = by_name["propose_join"].inputSchema
    assert {"action", "id", "notes", "relationship"} <= set(join_schema["properties"])
    assert "add" in json.dumps(join_schema["properties"]["action"])
    assert by_name["propose_join"].annotations.readOnlyHint is False
    assert by_name["list_joins"].annotations.readOnlyHint is True
    # Agents are told to try a composition with compile_and_run first, then propose it.
    assert "compile_and_run" in descriptions.PROPOSE_DEFINITION
    assert "composition" in descriptions.INSTRUCTIONS
    assert by_name["search"].annotations.readOnlyHint is True
    assert by_name["propose_definition"].annotations.readOnlyHint is False
    assert by_name["list_proposals"].annotations.readOnlyHint is True
    assert by_name["review_proposal"].annotations.readOnlyHint is False


@pytest.mark.anyio
async def test_admin_tools_carry_annotations(ctx: AppContext) -> None:
    async with client(ctx, ADMIN_AGENT) as session:
        by_name = {t.name: t for t in (await session.list_tools()).tools}
    assert set(by_name) == set(TOOL_NAMES)
    assert by_name["list_users"].annotations.readOnlyHint is True
    for name in ("manage_user", "manage_role", "review_proposal"):
        hints = by_name[name].annotations
        assert hints.readOnlyHint is False and hints.destructiveHint is True
    for tool in by_name.values():
        assert tool.description and len(tool.description) > 80


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
async def test_discovery_tools_list_joins_and_tables(ctx: AppContext) -> None:
    async with client(ctx, AGENT) as session:
        joins = ok(await session.call_tool("list_joins", {"table": "deals"}))["result"]
        known = ok(await session.call_tool("list_joins", {"known_only": True}))["result"]
        tables = ok(await session.call_tool("describe_tables", {"tables": ["deals"]}))["result"]
        gone = await session.call_tool("list_recipes", {})
    assert joins and all("deals" in (j["left_table"], j["right_table"]) for j in joins)
    edge = joins[0]
    assert edge["known"] and edge["sources"] == ["realization_rate"]
    assert {"relationship", "traps", "notes", "confirmed", "join_type"} <= set(edge)
    assert len(known) == len(ctx.store.list_joins())
    assert tables[0]["found"] and "amount" in {c["name"] for c in tables[0]["columns"]}
    assert gone.isError and "Unknown tool: list_recipes" in gone.content[0].text


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
async def test_propose_join_and_composed_definition(ctx: AppContext) -> None:
    annotate = {
        "action": "annotate",
        "left_table": "deals",
        "right_table": "invoices",
        "keys": [{"left": "deal_id", "right": "deal_id"}],
        "relationship": "one_to_many",
        "traps": ["fan-out: one deal has many invoices"],
    }
    add = {
        "action": "add",
        "left_table": "accounts",
        "right_table": "invoices",
        "keys": [{"left": "account_id", "right": "account_id"}],
    }
    composition = {
        "uses": {"rr": {"id": "realization_rate"}},
        "select": "SELECT rr.region, rr.realization_rate FROM rr",
        "fixed": {"rr": {"regions": ["APAC"]}},
    }
    metadata = {"id": "apac_rr", "title": "APAC realization"}
    async with client(ctx, AGENT) as session:
        joined = ok(await session.call_tool("propose_join", annotate))
        added = ok(await session.call_tool("propose_join", add))
        tried = ok(
            await session.call_tool(
                "compile_and_run",
                {"uses": composition["uses"], "select": composition["select"], "run": False},
            )
        )
        saved = ok(
            await session.call_tool(
                "propose_definition",
                {"composition": composition, "metadata": metadata, "dialect": "duckdb"},
            )
        )
        rerun = ok(
            await session.call_tool(
                "compile_and_run",
                {
                    "uses": {"a": {"id": "apac_rr"}},
                    "values": {"a": {"rr.period": Q3_APAC["period"]}},
                },
            )
        )
        card = ok(await session.call_tool("get_definition", {"id": "apac_rr"}))
        gone = await session.call_tool("propose_recipe", {"recipe": {}})
    assert joined["proposal"]["kind"] == "new_join" and joined["auto_applied"]
    assert joined["proposal"]["payload"]["evidence"]["observed"] == "one_to_many"
    assert not added["auto_applied"]  # an agent's new edge waits for a person (ADR 0026)
    assert tried["usable"] and "WITH" in tried["sql"]
    assert saved["proposal"]["kind"] == "new_definition" and saved["auto_applied"]
    assert rerun["row_count"] == 3 and rerun["label"] == TrustLabel.LLM_ANNOTATED.value
    assert card["composed"] and card["composition"]["fixed"] == {"rr": {"regions": ["APAC"]}}
    assert "rr.period" in [v["name"] for v in card["variables"]]
    assert gone.isError and "Unknown tool: propose_recipe" in gone.content[0].text


# ── Permissions and errors ───────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_viewer_cannot_propose_and_the_error_is_clean(ctx: AppContext) -> None:
    sql, metadata, annotations = _example("booked_vs_collected")
    args = {"sql": sql, "dialect": "duckdb", "metadata": metadata, "annotations": annotations}
    async with client(ctx, VIEWER_AGENT) as session:
        body, err = error(await session.call_tool("propose_definition", args))
    assert body.startswith("PermissionDeniedError: ")
    assert err == {"type": "PermissionDeniedError", "message": err["message"], "issues": []}
    assert "propose_definition" in err["message"]
    assert ctx.store.get_definition("booked_vs_collected") is None
    event = ctx.audit.list_events(limit=1)[0]
    assert event.actor == VIEWER_AGENT.name and event.extra["op"] == "tool_refused"


@pytest.mark.anyio
async def test_an_agent_approver_has_no_way_to_verify(ctx: AppContext) -> None:
    async with client(ctx, AGENT_APPROVER) as session:
        names = {t.name for t in (await session.list_tools()).tools}
        result = await session.call_tool("verify_definition", {"id": "realization_rate"})
        verify = await session.call_tool(
            "review_proposal",
            {"action": "verify", "definition_id": "realization_rate", "version": 1},
        )
    assert "review_proposal" in names and "manage_user" not in names
    assert result.isError and "Unknown tool" in text_of(result)
    body, err = error(verify)
    assert err["type"] == "PermissionDeniedError" and "verify" in body
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
    current = [AGENT]
    server = build_server(ctx, lambda: current[0])
    async with create_connected_server_and_client_session(server) as session:
        await session.call_tool("search", {"query": "realization"})
        current[0] = VIEWER_AGENT
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


def _stdio_params(db_url: str, user: str, token: str | None = None) -> Any:
    from mcp.client.stdio import StdioServerParameters

    env = dict(os.environ) | {"CTEFORGE_DB_URL": db_url, "CTEFORGE_USER": user}
    env.pop("CTEFORGE_CONNECTIONS", None)
    env.pop("CTEFORGE_TOKEN", None)
    if token is not None:
        env["CTEFORGE_TOKEN"] = token
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
        names = {t.name for t in (await session.list_tools()).tools}
        hits = await session.call_tool("search", {"query": "realization"})
    # CTEFORGE_USER without a token: the role's defaults (a contributor's 14 tools).
    assert names == set(TOOL_NAMES) - {"list_users", "manage_user", "manage_role"}
    assert ok(hits)["result"] == []
    with open_context(AppConfig(db_url=db_url)) as context:
        jo = context.store.get_user("jo")
        assert jo.role == UserRole.CONTRIBUTOR and jo.kind is ActorKind.AGENT  # as stored
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


@pytest.mark.anyio
async def test_run_stdio_with_a_token_shows_only_its_tools(tmp_path: Path) -> None:
    from mcp.client.stdio import stdio_client

    from cteforge.services import admin

    db_url = f"sqlite:///{tmp_path / 'stdio.db'}"
    with open_context(AppConfig(db_url=db_url)) as context:
        root, _ = admin.bootstrap_admin(context, "root")
        admin.create_user(context, root, "jo", UserRole.CONTRIBUTOR, ActorKind.AGENT)
        narrow = admin.create_token(
            context, root, "jo", name="support", tool_mode="only", include=["search"]
        )
    # CTEFORGE_USER names someone else: the token decides who and what.
    params = _stdio_params(db_url, "root", token=narrow.token)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        names = [t.name for t in (await session.list_tools()).tools]
        hidden = await session.call_tool("check_and_run", {"sql": "SELECT 1"})
    assert names == ["search"]
    body, err = error(hidden)
    assert err["type"] == "PermissionDeniedError" and "check_and_run" in body
    with open_context(AppConfig(db_url=db_url)) as context:
        event = context.audit.list_events(limit=1)[0]
        assert event.actor == "jo" and event.extra == {
            "op": "tool_refused",
            "tool": "check_and_run",
            "token_id": narrow.info.id,
            "token_prefix": narrow.token[:12],
        }


# ── Tool access (ADR 0027) ───────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_tools_list_follows_the_callers_tools(ctx: AppContext) -> None:
    narrow = AGENT.model_copy(update={"tools": frozenset({Tool.SEARCH, Tool.VERIFY})})
    async with client(ctx, narrow) as session:
        names = sorted(t.name for t in (await session.list_tools()).tools)
        hidden = await session.call_tool("compile_and_run", {"uses": {}})
        shown = await session.call_tool("search", {"query": "realization"})
    assert names == ["review_proposal", "search"]  # verify shows review_proposal
    body, err = error(hidden)
    assert err["type"] == "PermissionDeniedError" and "compile_and_run" in body
    assert ok(shown)["result"][0]["id"] == "realization_rate"
    refused = [e for e in ctx.audit.list_events(limit=5) if e.extra.get("op") == "tool_refused"]
    assert refused[0].outcome.value == "rejected" and refused[0].extra["tool"] == "compile_and_run"


@pytest.mark.anyio
async def test_no_caller_means_no_tools(ctx: AppContext) -> None:
    def refuse() -> User:
        raise PermissionDeniedError("Invalid or missing token")

    server = build_server(ctx, refuse)
    async with create_connected_server_and_client_session(server) as session:
        assert (await session.list_tools()).tools == []


@pytest.mark.anyio
async def test_review_proposal_by_an_admin_agent(ctx: AppContext) -> None:
    from cteforge.services import admin as admin_service
    from cteforge.services.propose import revise_definition

    admin_service.create_user(
        ctx, User(name="r", role=UserRole.ADMIN), "adminbot", "admin", "agent"
    )
    ctx.store.set_setting("mode", "strict")
    pending = revise_definition(
        ctx, ANALYST, id="realization_rate", base_version=1, metadata={"meaning": "x"}
    )
    other = revise_definition(
        ctx, ANALYST, id="realization_rate", base_version=1, metadata={"meaning": "y"}
    )
    mine = revise_definition(
        ctx, ADMIN_AGENT, id="realization_rate", base_version=1, metadata={"meaning": "z"}
    )
    async with client(ctx, ADMIN_AGENT) as session:
        listed = ok(await session.call_tool("list_proposals", {}))["result"]
        assert {p["id"] for p in listed} >= {pending.proposal.id, mine.proposal.id}
        own_body, own = error(
            await session.call_tool(
                "review_proposal", {"action": "approve", "id": mine.proposal.id}
            )
        )
        rejected = ok(
            await session.call_tool(
                "review_proposal",
                {"action": "reject", "id": other.proposal.id, "comment": "duplicate"},
            )
        )
        verified = ok(
            await session.call_tool(
                "review_proposal", {"action": "verify", "id": pending.proposal.id}
            )
        )
        missing_body, missing = error(
            await session.call_tool("review_proposal", {"action": "approve"})
        )
    assert own["type"] == "PermissionDeniedError" and "four-eyes" in own_body
    assert rejected["proposal"]["status"] == "rejected"
    assert rejected["proposal"]["decided_by_kind"] == "agent"
    assert verified["outcome"]["definition"]["state"] == "verified"
    assert ctx.store.get_definition("realization_rate").verified_by == "adminbot"
    assert missing["type"] == "PolicyError"


@pytest.mark.anyio
async def test_admin_agent_manages_users_and_roles_without_tokens(ctx: AppContext) -> None:
    from cteforge.services import admin as admin_service

    root, _ = admin_service.bootstrap_admin(ctx, "root")
    admin_service.create_user(ctx, root, "adminbot", "admin", "agent")
    async with client(ctx, ADMIN_AGENT) as session:
        created = ok(
            await session.call_tool(
                "manage_user", {"action": "create", "name": "helper", "role": "viewer"}
            )
        )
        boss_body, boss = error(
            await session.call_tool(
                "manage_user",
                {"action": "create", "name": "boss", "role": "admin", "kind": "human"},
            )
        )
        role = ok(
            await session.call_tool(
                "manage_role",
                {"action": "create", "name": "support_bot", "allowed": ["search"]},
            )
        )
        moved = ok(
            await session.call_tool(
                "manage_user", {"action": "set_role", "name": "helper", "role": "support_bot"}
            )
        )
        off = ok(await session.call_tool("manage_user", {"action": "deactivate", "name": "helper"}))
        root_off_body, root_off = error(
            await session.call_tool("manage_user", {"action": "deactivate", "name": "root"})
        )
        admin_role_body, admin_role = error(
            await session.call_tool(
                "manage_role", {"action": "edit", "name": "admin", "defaults": ["search"]}
            )
        )
        overview = ok(await session.call_tool("list_users", {}))
    assert created["user"]["name"] == "helper" and created["user"]["kind"] == "agent"
    assert created["token"] is None and created["user"]["tokens"] == []
    assert boss["type"] == "PermissionDeniedError" and ctx.store.get_user("boss") is None
    assert role["role"]["allowed"] == ["search"]
    assert moved["user"]["role"] == "support_bot" and off["user"]["active"] is False
    assert root_off["type"] == "PermissionDeniedError"
    assert ctx.store.get_user("root").active is True
    assert admin_role["type"] == "PermissionDeniedError"
    names = {u["name"] for u in overview["users"]}
    assert names == {"root", "adminbot", "helper"}
    root_view = next(u for u in overview["users"] if u["name"] == "root")
    assert root_view["tokens"][0]["prefix"].startswith("cfg_pat_")
    assert "support_bot" in {r["name"] for r in overview["roles"]}
    assert ctx.store.list_tokens("helper") == []
    assert not any(n.startswith("token") for n in TOOL_NAMES)  # no tool returns a token


@pytest.mark.anyio
async def test_compile_and_run_final_select_over_raw_tables_needs_check_and_run(
    ctx: AppContext,
) -> None:
    narrow = AGENT.model_copy(update={"tools": frozenset({Tool.SEARCH, Tool.COMPILE_AND_RUN})})
    args = {
        "uses": {"rr": {"id": "realization_rate"}},
        "values": {"rr": Q3_APAC},
        "select": "SELECT a.name, (SELECT COUNT(*) FROM rr) AS n FROM accounts AS a",
    }
    async with client(ctx, narrow) as session:
        body, err = error(await session.call_tool("compile_and_run", args))
        aliases_only = await session.call_tool(
            "compile_and_run", args | {"select": "SELECT * FROM rr"}
        )
    assert err["type"] == "PermissionDeniedError" and "check_and_run" in body
    assert ok(aliases_only)["ran"] is True
