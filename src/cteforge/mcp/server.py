"""MCP server (ADR 0018, 0027, DESIGN §6): seventeen tools, each one service call.

- `build_server(ctx, resolve_user)` registers the tools on a FastMCP server. Every call
  resolves the acting user (`resolve_user()`), calls exactly one service function and
  returns its card model (cards.py) as structured output.
- Tool access (ADR 0027): `tools/list` shows only the tools the caller's token (or local
  user) allows (`MCP_TOOL_ACCESS`; services.auth.user_tools). Calling any other tool is
  refused with a PermissionDeniedError tool error and audited (`auth.refuse_tool`), before
  the tool runs; the services check the same tools again.
- Errors: a CteforgeError becomes a tool error whose text is "<ErrorType>: message" plus,
  when the error carries any, its issues as JSON; `structuredContent` holds the same as
  {"error": {"type", "message", "issues"}} (the HTTP API's error shape). Invalid arguments
  → "InvalidArguments: ...". Anything else → a generic "InternalError" (details only in
  the server log). Never tracebacks, SQL connection strings or credentials.
- Service calls block (store, warehouse), so they run in a worker thread; the thread sees
  the caller's contextvars, and `current_request()` returns the HTTP request carrying the
  MCP message (None over stdio).
- `run_stdio()`: the local server behind `cteforge mcp`, acting as CTEFORGE_TOKEN's user
  and tools when set, else CTEFORGE_USER with its role's defaults (`auth.stdio_user`).
- `http_app(server)`: the streamable-HTTP ASGI app the API mounts (see its docstring for
  the lifespan requirement).
"""

from __future__ import annotations

import contextvars
import json
import logging
from collections.abc import Callable
from typing import Annotated, Any, Literal

import anyio.to_thread
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from mcp.types import Tool as MCPTool
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.applications import Starlette

from cteforge.domain.access import Tool, ToolMode
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import CteforgeError, PermissionDeniedError, PolicyError
from cteforge.domain.models import Annotation, DefinitionRef, JoinKey
from cteforge.domain.proposals import ProposalStatus
from cteforge.domain.users import ActorKind, User
from cteforge.mcp import descriptions as text
from cteforge.services import (
    admin,
    auth,
    catalog,
    describe,
    parse_query,
    propose,
    propose_other,
    review,
    search,
    search_index,
)
from cteforge.services import run as run_service
from cteforge.services.cards import (
    DefinitionCard,
    JoinCard,
    ParseReport,
    ProposalOutcome,
    ProposalView,
    RoleCard,
    RunOutcome,
    SearchHit,
    TableCard,
    TokenCard,
    UserView,
    json_safe,
)
from cteforge.services.context import AppConfig, AppContext, open_context
from cteforge.services.propose import CompositionInput, DefinitionMetadata

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
    "list_joins",
    "describe_tables",
    "list_proposals",
    "review_proposal",
    "list_users",
    "manage_user",
    "manage_role",
)

# Which of the caller's tools (domain/access.py) show each MCP tool (ADR 0027). Every MCP
# tool is the operation of the same name; review_proposal also shows for the verify tool
# (its "verify" action).
MCP_TOOL_ACCESS: dict[str, frozenset[Tool]] = {
    name: frozenset({Tool(name)}) for name in TOOL_NAMES if name != "review_proposal"
} | {"review_proposal": frozenset({Tool.REVIEW_PROPOSAL, Tool.VERIFY})}


def visible_tools(tools: frozenset[Tool]) -> frozenset[str]:
    """The MCP tool names a caller holding `tools` may see and call."""
    return frozenset(name for name, needs in MCP_TOOL_ACCESS.items() if needs & tools)


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
    "Error executing tool ..." and pass any exception text through), shows each caller
    only the tools it may use, and refuses (and audits) calls to the others."""

    cteforge_ctx: AppContext | None = None
    cteforge_resolve_user: ResolveUser | None = None

    async def _as_caller[T](self, work: Callable[[User], T]) -> T:
        """Resolve the caller (in a worker thread, with the HTTP request visible to
        `current_request()`) and run `work` with it there."""
        resolve = self.cteforge_resolve_user
        if resolve is None:
            raise RuntimeError("CteforgeMCP has no resolve_user")
        request = _http_request(self.get_context())

        def run() -> T:
            token = _REQUEST.set(request)
            try:
                return work(resolve())
            finally:
                _REQUEST.reset(token)

        return await anyio.to_thread.run_sync(run)

    async def list_tools(self) -> list[MCPTool]:
        tools = await super().list_tools()
        try:
            visible = await self._as_caller(lambda user: visible_tools(auth.user_tools(user)))
        except CteforgeError:
            return []  # No (valid) caller: show nothing.
        except Exception:  # noqa: BLE001 - never leak details through tools/list
            logger.error("Unexpected error resolving the MCP caller", exc_info=True)
            return []
        return [t for t in tools if t.name in visible]

    async def _refusal(self, name: str) -> BaseException | None:
        ctx = self.cteforge_ctx

        def check(user: User) -> BaseException | None:
            if name in visible_tools(auth.user_tools(user)):
                return None
            if ctx is None:
                return PermissionDeniedError(f"Tool '{name}' is not available")
            return auth.refuse_tool(ctx, user, name)

        try:
            return await self._as_caller(check)
        except Exception as exc:  # noqa: BLE001 - reported by error_result, never raw
            return exc

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if name in MCP_TOOL_ACCESS:
            refusal = await self._refusal(name)
            if refusal is not None:
                return error_result(refusal)
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
# Writes that can remove or lock out (reject, deactivate, revoke, delete a role).
_DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False
)

_CARD = ConfigDict(extra="forbid", frozen=True)


class ReviewResult(BaseModel):
    """What `review_proposal` changed: the approval outcome, the rejected proposal, or
    the verified definition (one of them)."""

    model_config = _CARD

    action: str
    outcome: ProposalOutcome | None = None
    proposal: ProposalView | None = None
    definition: DefinitionCard | None = None


class AccessOverview(BaseModel):
    """`list_users`: users (with token metadata, never values) and roles."""

    model_config = _CARD

    users: list[UserView]
    roles: list[RoleCard]


class ManageUserResult(BaseModel):
    """`manage_user`: the user as it is now, and the token changed (set_tools, revoke)."""

    model_config = _CARD

    action: str
    user: UserView | None = None
    token: TokenCard | None = None


class ManageRoleResult(BaseModel):
    """`manage_role`: the role as it is now (None after delete)."""

    model_config = _CARD

    action: str
    role: RoleCard | None = None
    deleted: bool = False


# Shared parameter docs.
_Question = Annotated[
    str | None,
    Field(description="The user's question in their words; recorded in the audit log."),
]
_Profile = Annotated[
    str | None,
    Field(description="Warehouse profile name; omit for the team's default connection."),
]
_Summary = Annotated[str, Field(description="One line for reviewers: what and why.")]
_SourceDialect = Annotated[
    Dialect | None,
    Field(
        description=(
            "The SQL's dialect. Omit to use the connection's dialect (the profile you "
            "pass, else the team's default connection)."
        )
    ),
]
_Composition = Annotated[
    CompositionInput | None,
    Field(
        description=(
            'A definition built from others: {"uses": {alias: {"id", "version"?}}, '
            '"select"?: final read-only SELECT over the aliases, "fixed"?: {alias: '
            "{variable: value}}}. Give `sql` or `composition`, not both."
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
    """The cteforge MCP server with its seventeen tools (DESIGN §6), each shown only to
    callers allowed to use it (ADR 0027). `resolve_user` is called per tool call (and per
    tools/list), in the calling request's context. `transport_security`: Host/Origin
    checks for streamable HTTP (None = the SDK default: DNS-rebinding protection allowing
    localhost only)."""
    server = CteforgeMCP(
        name=text.SERVER_NAME,
        instructions=text.INSTRUCTIONS,
        stateless_http=True,  # Each HTTP request runs in its own context (see http_app).
        transport_security=transport_security,
        warn_on_duplicate_tools=False,
    )
    server.cteforge_ctx = ctx
    server.cteforge_resolve_user = resolve_user

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
        include_search_document: Annotated[
            bool, Field(description="Also return the Markdown text meaning search uses.")
        ] = False,
    ) -> DefinitionCard:
        return await _call(
            mcp_context,
            resolve_user,
            lambda user: describe.get_definition(
                ctx, user, id, version, include_search_document=include_search_document
            ),
        )

    async def compile_and_run_tool(
        mcp_context: Context,
        uses: Annotated[
            dict[str, DefinitionRef] | None,
            Field(
                description='alias -> {"id": <definition id>, "version"?: <int>}; composed '
                "definitions too"
            ),
        ] = None,
        values: Annotated[
            dict[str, dict[str, Any]] | None,
            Field(
                description="alias -> {variable name: value in its input_format}; a composed "
                'definition\'s variables are "<part alias>.<variable>"'
            ),
        ] = None,
        select: Annotated[
            str | None,
            Field(description="Optional final read-only SELECT over the aliases."),
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
        mcp_context: Context,
        dialect: _SourceDialect = None,
        profile: _Profile = None,
    ) -> ParseReport:
        return await _call(
            mcp_context,
            resolve_user,
            lambda user: parse_query.parse_query(ctx, user, sql, dialect, profile),
        )

    async def propose_definition_tool(
        metadata: _Metadata,
        mcp_context: Context,
        sql: Annotated[
            str | None,
            Field(description="The trusted query exactly as the user gave it (or `composition`)."),
        ] = None,
        composition: _Composition = None,
        annotations: Annotated[
            list[Annotation] | None,
            Field(
                description="Variables of `sql` (or of the composition's final SELECT): "
                "{name, type, role ('fixed' default | 'changeable'), literals: [literal IDs "
                "from parse_query], column?, description?, allowed?, format?, unit?}. "
                "type: date_range, date, text_list, text, number, boolean, grain. Not needed "
                "for a composition's parts."
            ),
        ] = None,
        dialect: _SourceDialect = None,
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
                composition=composition,
                dialect=dialect,
                metadata=metadata,
                annotations=annotations or [],
                profile=profile,
                summary=summary,
            ),
        )

    async def revise_definition_tool(
        id: Annotated[str, Field(description="The definition to revise.")],
        base_version: Annotated[int, Field(ge=1, description="Its current version.")],
        mcp_context: Context,
        sql: Annotated[str | None, Field(description="New SQL, if the SQL changes.")] = None,
        composition: _Composition = None,
        dialect: Annotated[
            Dialect | None, Field(description="Dialect of `sql`; default the current one.")
        ] = None,
        metadata: Annotated[
            DefinitionMetadata | None,
            Field(description="Only the keys to change (not id: use new_id to rename)."),
        ] = None,
        annotations: Annotated[
            list[Annotation] | None,
            Field(description="The full new list of variables, if they change."),
        ] = None,
        summary: _Summary = "",
        new_id: Annotated[
            str | None,
            Field(
                description="Propose renaming the definition to this ID instead of a new "
                "version. Pass nothing else to change."
            ),
        ] = None,
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
                composition=composition,
                dialect=dialect,
                metadata=metadata,
                annotations=annotations,
                summary=summary,
                new_id=new_id,
            ),
        )

    async def propose_join_tool(
        mcp_context: Context,
        action: Annotated[
            Literal["add", "annotate"],
            Field(
                description="annotate: relationship / traps / notes on an existing edge (by "
                "`id`, or tables + keys). add: an edge no definition contains."
            ),
        ] = "annotate",
        id: Annotated[str | None, Field(description="annotate: the edge id (list_joins).")] = None,
        left_table: Annotated[str | None, Field(description="Table name, as in the SQL.")] = None,
        right_table: Annotated[str | None, Field(description="Table name, as in the SQL.")] = None,
        keys: Annotated[
            list[JoinKey] | None,
            Field(description='[{"left": <left_table column>, "right": <right_table column>}]'),
        ] = None,
        join_type: Annotated[
            str | None, Field(description="add: inner (default), left, right or full.")
        ] = None,
        relationship: Annotated[
            str | None,
            Field(
                description="one_to_one, one_to_many, many_to_one or many_to_many, from "
                "left_table to right_table (by `id`: in the edge's own orientation). "
                "Checked against the warehouse."
            ),
        ] = None,
        traps: Annotated[
            list[str] | None, Field(description="Known pitfalls, in plain words.")
        ] = None,
        notes: Annotated[str | None, Field(description="Anything else people should know.")] = None,
        summary: _Summary = "",
    ) -> ProposalOutcome:
        return await _call(
            mcp_context,
            resolve_user,
            lambda user: propose_other.propose_join(
                ctx,
                user,
                action=action,
                id=id,
                left_table=left_table,
                right_table=right_table,
                keys=keys,
                join_type=join_type,
                relationship=relationship,
                traps=traps,
                notes=notes,
                summary=summary,
            ),
        )

    async def list_joins_tool(
        mcp_context: Context,
        table: Annotated[
            str | None, Field(description="Only edges touching this table; omit for all.")
        ] = None,
        known_only: Annotated[
            bool, Field(description="Only edges that count as known for trust labels.")
        ] = False,
    ) -> list[JoinCard]:
        return await _call(
            mcp_context,
            resolve_user,
            lambda user: catalog.list_joins(ctx, user, table=table, known_only=known_only),
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

    async def list_proposals_tool(
        mcp_context: Context,
        status: Annotated[
            ProposalStatus | None,
            Field(description="pending (default), approved, rejected or withdrawn."),
        ] = ProposalStatus.PENDING,
    ) -> list[ProposalView]:
        return await _call(
            mcp_context,
            resolve_user,
            lambda user: review.list_proposals(ctx, user, status),
        )

    async def review_proposal_tool(
        action: Annotated[
            Literal["approve", "reject", "verify"],
            Field(description="approve | reject | verify"),
        ],
        mcp_context: Context,
        id: Annotated[
            str | None, Field(description="Proposal id (approve, reject, verify a proposal).")
        ] = None,
        comment: Annotated[
            str, Field(description="Why (required to reject); kept on the proposal.")
        ] = "",
        definition_id: Annotated[
            str | None, Field(description="verify only: an applied definition's id.")
        ] = None,
        version: Annotated[
            int | None, Field(ge=1, description="verify only: its latest version.")
        ] = None,
    ) -> ReviewResult:
        def work(user: User) -> ReviewResult:
            if action == "verify" and id is None:
                if definition_id is None or version is None:
                    raise PolicyError("verify needs `id`, or `definition_id` and `version`")
                card = review.verify_definition(ctx, user, definition_id, version)
                return ReviewResult(action=action, definition=card)
            if id is None:
                raise PolicyError(f"{action} needs the proposal `id`")
            if action == "reject":
                return ReviewResult(action=action, proposal=review.reject(ctx, user, id, comment))
            outcome = review.approve(ctx, user, id, verify=action == "verify", comment=comment)
            return ReviewResult(action=action, outcome=outcome)

        return await _call(mcp_context, resolve_user, work)

    async def list_users_tool(mcp_context: Context) -> AccessOverview:
        return await _call(
            mcp_context,
            resolve_user,
            lambda user: AccessOverview(
                users=admin.list_users(ctx, user), roles=admin.list_roles(ctx, user)
            ),
        )

    async def manage_user_tool(
        action: Annotated[
            Literal["create", "set_role", "set_tools", "deactivate", "activate", "revoke_token"],
            Field(
                description="create | set_role | set_tools | deactivate | activate | revoke_token"
            ),
        ],
        name: Annotated[str, Field(description="The user's login name.")],
        mcp_context: Context,
        role: Annotated[str | None, Field(description="create, set_role: a role name.")] = None,
        kind: Annotated[ActorKind, Field(description="create: human or agent.")] = ActorKind.AGENT,
        token_id: Annotated[
            str | None, Field(description="set_tools, revoke_token: the token's id.")
        ] = None,
        tool_mode: Annotated[
            ToolMode | None, Field(description="set_tools: follow_role or only.")
        ] = None,
        include: Annotated[
            list[Tool] | None, Field(description="set_tools: tools to add / the only tools.")
        ] = None,
        exclude: Annotated[
            list[Tool] | None, Field(description="set_tools (follow_role): tools to remove.")
        ] = None,
    ) -> ManageUserResult:
        def view(user: User) -> UserView:
            return UserView(
                name=user.name,
                role=user.role,
                kind=user.kind,
                active=user.active,
                created_at=user.created_at,
            )

        def work(actor: User) -> ManageUserResult:
            if action == "create":
                if role is None:
                    raise PolicyError("create needs a `role`")
                created, _ = admin.create_user(ctx, actor, name, role, kind, issue_token=False)
                return ManageUserResult(action=action, user=view(created))
            if action == "set_role":
                if role is None:
                    raise PolicyError("set_role needs a `role`")
                return ManageUserResult(
                    action=action, user=admin.update_user(ctx, actor, name, role=role)
                )
            if action in ("deactivate", "activate"):
                updated = admin.update_user(ctx, actor, name, active=action == "activate")
                return ManageUserResult(action=action, user=updated)
            if token_id is None:
                raise PolicyError(f"{action} needs a `token_id`")
            if action == "revoke_token":
                card = admin.revoke_token(ctx, actor, token_id, name)
                return ManageUserResult(action=action, token=card)
            if tool_mode is None:
                raise PolicyError("set_tools needs a `tool_mode`")
            card = admin.set_token_tools(
                ctx,
                actor,
                token_id,
                name,
                tool_mode=tool_mode,
                include=include or [],
                exclude=exclude or [],
            )
            return ManageUserResult(action=action, token=card)

        return await _call(mcp_context, resolve_user, work)

    async def manage_role_tool(
        action: Annotated[
            Literal["create", "edit", "delete"], Field(description="create | edit | delete")
        ],
        name: Annotated[str, Field(description="The role's name (lower_snake_case).")],
        mcp_context: Context,
        description: Annotated[str | None, Field(description="What the role is for.")] = None,
        allowed: Annotated[
            list[Tool] | None, Field(description="Every tool the role may ever use.")
        ] = None,
        defaults: Annotated[
            list[Tool] | None,
            Field(description="Tools a new token starts with (subset of allowed)."),
        ] = None,
        review_all: Annotated[
            bool | None,
            Field(description="Approve any proposal (else only what the user owns)."),
        ] = None,
    ) -> ManageRoleResult:
        def work(actor: User) -> ManageRoleResult:
            if action == "create":
                card = admin.create_role(
                    ctx,
                    actor,
                    name,
                    description=description or "",
                    allowed=allowed or [],
                    defaults=defaults,
                    review_all=bool(review_all),
                )
                return ManageRoleResult(action=action, role=card)
            if action == "edit":
                card = admin.update_role(
                    ctx,
                    actor,
                    name,
                    description=description,
                    allowed=allowed,
                    defaults=defaults,
                    review_all=review_all,
                )
                return ManageRoleResult(action=action, role=card)
            admin.delete_role(ctx, actor, name)
            return ManageRoleResult(action=action, deleted=True)

        return await _call(mcp_context, resolve_user, work)

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
        (propose_join_tool, "propose_join", "Propose a join change", text.PROPOSE_JOIN, _WRITE),
        (list_joins_tool, "list_joins", "List the join map", text.LIST_JOINS, _READ),
        (
            describe_tables_tool,
            "describe_tables",
            "Describe tables and columns",
            text.DESCRIBE_TABLES,
            _READ,
        ),
        (list_proposals_tool, "list_proposals", "List proposals", text.LIST_PROPOSALS, _READ),
        (
            review_proposal_tool,
            "review_proposal",
            "Approve, reject or verify",
            text.REVIEW_PROPOSAL,
            _DESTRUCTIVE,
        ),
        (list_users_tool, "list_users", "List users and roles", text.LIST_USERS, _READ),
        (manage_user_tool, "manage_user", "Manage a user", text.MANAGE_USER, _DESTRUCTIVE),
        (manage_role_tool, "manage_role", "Manage a role", text.MANAGE_ROLE, _DESTRUCTIVE),
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
    """`cteforge mcp`: serve over stdio (ADR 0018, 0027) as CTEFORGE_TOKEN's user with
    that token's tools when set, else as CTEFORGE_USER with its role's default tools
    (`services.auth.stdio_user`). The user must already exist: an empty store is refused
    instead of turning the MCP client into its first human admin.

    Opens the context from the environment and resolves the user once at start, so a bad
    token or CTEFORGE_USER fails before serving (PermissionDeniedError propagates to the
    caller); each call then re-resolves, so revocation and role changes apply at once."""
    ctx = open_context(AppConfig.from_env())
    try:
        # Never creates the user: whoever is on the other end of stdio is an MCP client,
        # usually an agent, and must not become the first human admin of an empty store.
        auth.stdio_user(ctx)
        # Meaning search (ADR 0023): load the model in the background, never in a call.
        search_index.start_background(ctx)
        server = build_server(ctx, lambda: auth.stdio_user(ctx))
        server.run("stdio")
    finally:
        ctx.close()
