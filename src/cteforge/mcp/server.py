"""MCP server (ADR 0018, DESIGN §6): twelve tools, each one service call.

- `build_server(ctx, resolve_user)` registers the tools on a FastMCP server. Every call
  resolves the acting user (`resolve_user()`), calls exactly one service function and
  returns its card model (cards.py) as structured output.
- Errors: a CteforgeError becomes a tool error whose text is "<ErrorType>: message" plus,
  when the error carries any, its issues as JSON; `structuredContent` holds the same as
  {"error": {"type", "message", "issues"}} (the HTTP API's error shape). Invalid arguments
  → "InvalidArguments: ...". Anything else → a generic "InternalError" (details only in
  the server log). Never tracebacks, SQL connection strings or credentials.
- Service calls block (store, warehouse), so they run in a worker thread; the thread sees
  the caller's contextvars, and `current_request()` returns the HTTP request carrying the
  MCP message (None over stdio).
- `run_stdio()`: the local server behind `cteforge mcp`, acting as CTEFORGE_USER.
- `http_app(server)`: the streamable-HTTP ASGI app the API mounts (see its docstring for
  the lifespan requirement).
"""

from __future__ import annotations

import contextvars
import json
import logging
from collections.abc import Callable
from typing import Annotated, Any

import anyio.to_thread
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field, ValidationError
from starlette.applications import Starlette

from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import CteforgeError
from cteforge.domain.models import Annotation, DefinitionRef, JoinEdge, JoinKey, Recipe
from cteforge.domain.users import User
from cteforge.mcp import descriptions as text
from cteforge.services import (
    auth,
    catalog,
    describe,
    parse_query,
    propose,
    propose_other,
    search,
)
from cteforge.services import run as run_service
from cteforge.services.cards import (
    DefinitionCard,
    ParseReport,
    ProposalOutcome,
    RunOutcome,
    SearchHit,
    TableCard,
    json_safe,
)
from cteforge.services.context import AppConfig, AppContext, open_context
from cteforge.services.propose import DefinitionMetadata

logger = logging.getLogger(__name__)

ResolveUser = Callable[[], User]

TOOL_NAMES = (
    "search",
    "get_definition",
    "compile_and_run",
    "check_and_run",
    "parse_query",
    "propose_definition",
    "revise_definition",
    "propose_join",
    "propose_recipe",
    "list_recipes",
    "list_joins",
    "describe_tables",
)

_REQUEST: contextvars.ContextVar[Any | None] = contextvars.ContextVar(
    "cteforge_mcp_request", default=None
)


def current_request() -> Any | None:
    """The HTTP request (a starlette Request) that carried the MCP message being handled,
    or None over stdio / in-memory transports. Set only while a tool call runs; meant for
    an HTTP `resolve_user` that reads the caller's bearer token."""
    return _REQUEST.get()


# ── Errors ───────────────────────────────────────────────────────────────────────────


def _invalid_arguments(exc: ValidationError) -> str:
    """One line per problem, without echoing the input values."""
    parts = []
    for error in exc.errors(include_url=False, include_input=False):
        where = ".".join(str(p) for p in error.get("loc", ())) or "arguments"
        parts.append(f"{where}: {error.get('msg', 'invalid')}")
    return "; ".join(parts) or "invalid arguments"


def error_result(error: BaseException) -> CallToolResult:
    """The tool error for a failed call (see module docstring)."""
    issues: list[Any] = []
    if isinstance(error, CteforgeError):
        kind, message = type(error).__name__, str(error)
        issues = [json_safe(i.model_dump()) for i in getattr(error, "issues", None) or []]
    elif isinstance(error, ValidationError):
        kind, message = "InvalidArguments", _invalid_arguments(error)
    else:
        logger.error("Unexpected error in an MCP tool call", exc_info=error)
        kind, message = "InternalError", "unexpected server error; details are in the server log"
    body = f"{kind}: {message}"
    if issues:
        body += "\nissues: " + json.dumps(issues)
    return CallToolResult(
        content=[TextContent(type="text", text=body)],
        structuredContent={"error": {"type": kind, "message": message, "issues": issues}},
        isError=True,
    )


class CteforgeMCP(FastMCP):
    """FastMCP that turns tool failures into `error_result` (FastMCP itself would prefix
    "Error executing tool ..." and pass any exception text through)."""

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        try:
            return await super().call_tool(name, arguments)
        except ToolError as exc:
            if exc.__cause__ is None:
                raise  # Unknown tool and similar protocol errors: FastMCP's own message.
            return error_result(exc.__cause__)


# ── Calls ────────────────────────────────────────────────────────────────────────────


def _http_request(mcp_context: Context) -> Any | None:
    try:
        return mcp_context.request_context.request
    except (ValueError, LookupError, AttributeError):
        return None


async def _call[T](
    mcp_context: Context, resolve_user: ResolveUser, use_case: Callable[[User], T]
) -> T:
    """Resolve the user and run one use-case in a worker thread (blocking I/O)."""
    request = _http_request(mcp_context)

    def work() -> T:
        token = _REQUEST.set(request)
        try:
            return use_case(resolve_user())
        finally:
            _REQUEST.reset(token)

    return await anyio.to_thread.run_sync(work)


_READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
_RUN = ToolAnnotations(readOnlyHint=True, idempotentHint=False, openWorldHint=False)
_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)

# Shared parameter docs.
_Question = Annotated[
    str | None,
    Field(description="The user's question in their words; recorded in the audit log."),
]
_Profile = Annotated[
    str | None,
    Field(description="Warehouse profile name; omit for the default profile."),
]
_Summary = Annotated[str, Field(description="One line for reviewers: what and why.")]
_Annotations = Annotated[
    list[Annotation],
    Field(
        description=(
            "One object per variable: {name, type, role ('fixed' default | 'changeable'), "
            "literals: [literal IDs from parse_query], column?, description?, allowed?, "
            "format?, unit?}. type: date_range, date, text_list, text, number, boolean, grain."
        )
    ),
]
_Metadata = Annotated[
    DefinitionMetadata,
    Field(
        description=(
            "{id, title, meaning?, keywords?, use_when?, dont_use_when?, grain?, join_keys?, "
            "owner?, profile?}"
        )
    ),
]


def build_server(
    ctx: AppContext,
    resolve_user: ResolveUser,
    *,
    transport_security: TransportSecuritySettings | None = None,
) -> FastMCP:
    """The cteforge MCP server with its twelve tools (DESIGN §6). No approve/verify tool
    exists (ADR 0007). `resolve_user` is called once per tool call, in the calling
    request's context. `transport_security`: Host/Origin checks for streamable HTTP
    (None = the SDK default: DNS-rebinding protection allowing localhost only)."""
    server = CteforgeMCP(
        name=text.SERVER_NAME,
        instructions=text.INSTRUCTIONS,
        stateless_http=True,  # Each HTTP request runs in its own context (see http_app).
        transport_security=transport_security,
        warn_on_duplicate_tools=False,
    )

    async def search_tool(
        query: Annotated[str, Field(description="Plain words from the user's question.")],
        mcp_context: Context,
        limit: Annotated[int, Field(ge=1, le=50, description="Maximum hits.")] = 10,
        include_drafts: Annotated[
            bool, Field(description="Also list drafts (they cannot be run).")
        ] = False,
        question: _Question = None,
    ) -> list[SearchHit]:
        return await _call(
            mcp_context,
            resolve_user,
            lambda user: search.search(
                ctx, user, query, limit=limit, include_drafts=include_drafts, question=question
            ),
        )

    async def get_definition_tool(
        id: Annotated[str, Field(description="Definition id, e.g. 'realization_rate'.")],
        mcp_context: Context,
        version: Annotated[int | None, Field(ge=1, description="Omit for the latest.")] = None,
    ) -> DefinitionCard:
        return await _call(
            mcp_context, resolve_user, lambda user: describe.get_definition(ctx, user, id, version)
        )

    async def compile_and_run_tool(
        mcp_context: Context,
        uses: Annotated[
            dict[str, DefinitionRef] | None,
            Field(description='alias -> {"id": <definition id>, "version"?: <int>}'),
        ] = None,
        values: Annotated[
            dict[str, dict[str, Any]] | None,
            Field(description="alias -> {variable name: value in its input_format}"),
        ] = None,
        select: Annotated[
            str | None,
            Field(description="Optional final read-only SELECT over the aliases."),
        ] = None,
        recipe_id: Annotated[
            str | None, Field(description="Run a saved recipe instead of `uses`.")
        ] = None,
        dialect: Annotated[
            Dialect | None,
            Field(
                description="Omit to use the profile's dialect. Another dialect needs "
                "run=false: the SQL is only compiled (transpiled, labeled, no EXPLAIN)."
            ),
        ] = None,
        profile: _Profile = None,
        run: Annotated[bool, Field(description="false = compile and label only.")] = True,
        limit: Annotated[int, Field(ge=1, description="Maximum rows returned.")] = 100,
        question: _Question = None,
    ) -> RunOutcome:
        return await _call(
            mcp_context,
            resolve_user,
            lambda user: run_service.compile_and_run(
                ctx,
                user,
                uses=uses,
                recipe_id=recipe_id,
                values=values,
                select=select,
                dialect=dialect,
                profile=profile,
                run=run,
                limit=limit,
                question=question,
            ),
        )

    async def check_and_run_tool(
        sql: Annotated[str, Field(description="One read-only SELECT.")],
        mcp_context: Context,
        profile: _Profile = None,
        run: Annotated[bool, Field(description="false = check and label only.")] = True,
        limit: Annotated[int, Field(ge=1, description="Maximum rows returned.")] = 100,
        question: _Question = None,
    ) -> RunOutcome:
        return await _call(
            mcp_context,
            resolve_user,
            lambda user: run_service.check_and_run(
                ctx, user, sql, profile=profile, run=run, limit=limit, question=question
            ),
        )

    async def parse_query_tool(
        sql: Annotated[str, Field(description="The trusted query, one statement.")],
        dialect: Annotated[Dialect, Field(description="The SQL's dialect.")],
        mcp_context: Context,
        profile: _Profile = None,
    ) -> ParseReport:
        return await _call(
            mcp_context,
            resolve_user,
            lambda user: parse_query.parse_query(ctx, user, sql, dialect, profile),
        )

    async def propose_definition_tool(
        sql: Annotated[str, Field(description="The trusted query exactly as the user gave it.")],
        dialect: Annotated[Dialect, Field(description="The SQL's dialect.")],
        metadata: _Metadata,
        annotations: _Annotations,
        mcp_context: Context,
        profile: _Profile = None,
        summary: _Summary = "",
    ) -> ProposalOutcome:
        return await _call(
            mcp_context,
            resolve_user,
            lambda user: propose.propose_definition(
                ctx,
                user,
                sql=sql,
                dialect=dialect,
                metadata=metadata,
                annotations=annotations,
                profile=profile,
                summary=summary,
            ),
        )

    async def revise_definition_tool(
        id: Annotated[str, Field(description="The definition to revise.")],
        base_version: Annotated[int, Field(ge=1, description="Its current version.")],
        mcp_context: Context,
        sql: Annotated[str | None, Field(description="New SQL, if the SQL changes.")] = None,
        dialect: Annotated[
            Dialect | None, Field(description="Dialect of `sql`; default the current one.")
        ] = None,
        metadata: Annotated[
            DefinitionMetadata | None,
            Field(description="Only the keys to change (id cannot change)."),
        ] = None,
        annotations: Annotated[
            list[Annotation] | None,
            Field(description="The full new list of variables, if they change."),
        ] = None,
        summary: _Summary = "",
    ) -> ProposalOutcome:
        return await _call(
            mcp_context,
            resolve_user,
            lambda user: propose.revise_definition(
                ctx,
                user,
                id=id,
                base_version=base_version,
                sql=sql,
                dialect=dialect,
                metadata=metadata,
                annotations=annotations,
                summary=summary,
            ),
        )

    async def propose_join_tool(
        left_table: Annotated[str, Field(description="Table name, as in the SQL.")],
        right_table: Annotated[str, Field(description="Table name, as in the SQL.")],
        keys: Annotated[
            list[JoinKey],
            Field(min_length=1, description='[{"left": <left column>, "right": <right column>}]'),
        ],
        mcp_context: Context,
        join_type: Annotated[str, Field(description="inner, left, right or full.")] = "inner",
        relationship: Annotated[
            str | None,
            Field(description="one_to_one, one_to_many, many_to_one or many_to_many."),
        ] = None,
        traps: Annotated[
            list[str] | None, Field(description="Known pitfalls, in plain words.")
        ] = None,
        summary: _Summary = "",
    ) -> ProposalOutcome:
        return await _call(
            mcp_context,
            resolve_user,
            lambda user: propose_other.propose_join(
                ctx,
                user,
                left_table=left_table,
                right_table=right_table,
                keys=keys,
                join_type=join_type,
                relationship=relationship,
                traps=traps or (),
                summary=summary,
            ),
        )

    async def propose_recipe_tool(
        recipe: Annotated[
            propose_other.RecipeInput,
            Field(description="{id, title, description?, uses, values?, select?, dialect}"),
        ],
        mcp_context: Context,
        summary: _Summary = "",
    ) -> ProposalOutcome:
        return await _call(
            mcp_context,
            resolve_user,
            lambda user: propose_other.propose_recipe(ctx, user, recipe=recipe, summary=summary),
        )

    async def list_recipes_tool(
        mcp_context: Context,
        query: Annotated[
            str, Field(description="Optional words to filter by (id, title, description).")
        ] = "",
        include_drafts: Annotated[
            bool, Field(description="Also list drafts (they cannot be run).")
        ] = False,
    ) -> list[Recipe]:
        return await _call(
            mcp_context,
            resolve_user,
            lambda user: catalog.list_recipes(
                ctx, user, query=query, include_drafts=include_drafts
            ),
        )

    async def list_joins_tool(
        mcp_context: Context,
        table: Annotated[
            str | None, Field(description="Only edges touching this table; omit for all.")
        ] = None,
        include_drafts: Annotated[
            bool, Field(description="Also list draft edges (they do not count as known).")
        ] = False,
    ) -> list[JoinEdge]:
        return await _call(
            mcp_context,
            resolve_user,
            lambda user: catalog.list_joins(ctx, user, table=table, include_drafts=include_drafts),
        )

    async def describe_tables_tool(
        mcp_context: Context,
        tables: Annotated[
            list[str] | None,
            Field(description="Table names; omit for every table the library knows."),
        ] = None,
        profile: _Profile = None,
    ) -> list[TableCard]:
        return await _call(
            mcp_context,
            resolve_user,
            lambda user: catalog.describe_tables(ctx, user, tables, profile=profile),
        )

    tools: list[tuple[Callable[..., Any], str, str, str, ToolAnnotations]] = [
        (search_tool, "search", "Search the library", text.SEARCH, _READ),
        (get_definition_tool, "get_definition", "Get a definition", text.GET_DEFINITION, _READ),
        (
            compile_and_run_tool,
            "compile_and_run",
            "Run definitions by reference",
            text.COMPILE_AND_RUN,
            _RUN,
        ),
        (check_and_run_tool, "check_and_run", "Run exploratory SQL", text.CHECK_AND_RUN, _RUN),
        (parse_query_tool, "parse_query", "Analyze a trusted query", text.PARSE_QUERY, _READ),
        (
            propose_definition_tool,
            "propose_definition",
            "Propose a definition",
            text.PROPOSE_DEFINITION,
            _WRITE,
        ),
        (
            revise_definition_tool,
            "revise_definition",
            "Propose a revision",
            text.REVISE_DEFINITION,
            _WRITE,
        ),
        (propose_join_tool, "propose_join", "Propose a join", text.PROPOSE_JOIN, _WRITE),
        (propose_recipe_tool, "propose_recipe", "Propose a recipe", text.PROPOSE_RECIPE, _WRITE),
        (list_recipes_tool, "list_recipes", "List saved recipes", text.LIST_RECIPES, _READ),
        (list_joins_tool, "list_joins", "List the join map", text.LIST_JOINS, _READ),
        (
            describe_tables_tool,
            "describe_tables",
            "Describe tables and columns",
            text.DESCRIBE_TABLES,
            _READ,
        ),
    ]
    for fn, name, title, description, hints in tools:
        server.add_tool(
            fn,
            name=name,
            title=title,
            description=description,
            annotations=hints,
            structured_output=True,
        )
    return server


# ── Transports ───────────────────────────────────────────────────────────────────────


def http_app(server: FastMCP, *, path: str = "/") -> Starlette:
    """The streamable-HTTP ASGI app of `server`, its endpoint at `path` inside the app.

    Mounting it in FastAPI (e.g. `app.mount("/mcp", http_app(server))` → endpoint
    `/mcp/`): Starlette does NOT run a mounted app's lifespan, so the parent app's lifespan
    must run the session manager, which exists once this function has been called:

        mcp_app = http_app(server)

        @asynccontextmanager
        async def lifespan(app):
            async with server.session_manager.run():
                yield

    The session manager can run once per server instance (build a new server per app).
    The server is stateless over HTTP (`stateless_http=True`): every POST runs in a task
    started from that request, so contextvars the API sets for the request (e.g. the user
    its auth resolved) are visible to `resolve_user`; `current_request()` also gives the
    request itself (headers) during a tool call.
    """
    server.settings.streamable_http_path = path
    return server.streamable_http_app()


def run_stdio() -> None:
    """`cteforge mcp`: serve over stdio as the local user CTEFORGE_USER (ADR 0018), which
    must already exist (`services.auth.local_user(create=False)`): an empty store is
    refused instead of turning the MCP client into its first human admin.

    Opens the context from the environment and resolves the user once at start, so a bad
    CTEFORGE_USER fails before serving (PermissionDeniedError propagates to the caller);
    each call then re-reads the user, so role changes apply at once."""
    ctx = open_context(AppConfig.from_env())
    try:
        # Never creates the user: whoever is on the other end of stdio is an MCP client,
        # usually an agent, and must not become the first human admin of an empty store.
        auth.local_user(ctx, create=False)
        server = build_server(ctx, lambda: auth.local_user(ctx, create=False))
        server.run("stdio")
    finally:
        ctx.close()
