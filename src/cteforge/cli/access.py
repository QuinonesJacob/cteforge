"""`token` and `role` command groups (ADR 0027): thin calls into services.admin.

The CLI is a person at the machine (CTEFORGE_USER with its role's allowed tools). A token
is printed once on stdout (the warning on stderr) and never written anywhere.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated

import typer

from cteforge.cli._common import app, echo_json, echo_token, session, table, usage
from cteforge.domain.access import Tool, ToolMode
from cteforge.services import admin

token_app = typer.Typer(no_args_is_help=True, help="Personal access tokens (ADR 0027).")
role_app = typer.Typer(no_args_is_help=True, help="Roles and their tools (admin).")
app.add_typer(token_app, name="token")
app.add_typer(role_app, name="role")

JsonOpt = Annotated[bool, typer.Option("--json", help="Print JSON")]
UserOpt = Annotated[
    str | None, typer.Option("--user", "-u", help="Whose tokens (default: you; admin)")
]


def _tools(values: list[str] | None) -> list[Tool] | None:
    """Repeated and/or comma-separated tool names → Tools (unknown → exit 2)."""
    if values is None:
        return None
    names = [n.strip() for v in values for n in v.split(",") if n.strip()]
    try:
        return [Tool(n) for n in names]
    except ValueError:
        usage(f"unknown tool; tools are: {', '.join(t.value for t in Tool)}")


def _when(value: str | None) -> dt.datetime | None:
    """ISO date (midnight UTC) or datetime (naive = UTC)."""
    if value is None:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        usage("--expires must be an ISO date or datetime, e.g. 2027-01-31")
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=dt.UTC)


def _short(value: dt.datetime | None) -> str:
    return value.isoformat(timespec="minutes") if value is not None else ""


# ── token ────────────────────────────────────────────────────────────────────────────


@token_app.command("create")
def token_create(
    name: Annotated[str, typer.Argument(help="What the token is for, e.g. 'Claude Code'")],
    user: UserOpt = None,
    expires: Annotated[
        str | None, typer.Option("--expires", help="Expiry: ISO date or datetime (UTC)")
    ] = None,
    only: Annotated[
        list[str] | None,
        typer.Option("--only", help="Only these tools (repeat or comma-separate)"),
    ] = None,
    include: Annotated[
        list[str] | None, typer.Option("--include", help="Follow the role, plus these tools")
    ] = None,
    exclude: Annotated[
        list[str] | None, typer.Option("--exclude", help="Follow the role, minus these tools")
    ] = None,
) -> None:
    """Generate a token (yours, or --user's as an admin) and print it ONCE."""
    if only is not None and (include or exclude):
        usage("give --only, or --include/--exclude (not both)")
    mode = ToolMode.ONLY if only is not None else ToolMode.FOLLOW_ROLE
    with session() as (ctx, acting):
        created = admin.create_token(
            ctx,
            acting,
            user,
            name=name,
            expires_at=_when(expires),
            tool_mode=mode,
            include=_tools(only if only is not None else include) or [],
            exclude=_tools(exclude) or [],
        )
    info = created.info
    typer.echo(
        f"token '{info.name}' ({info.prefix}…, id {info.id}) for '{info.user_name}': "
        f"{', '.join(info.tools) or 'no tools'}"
    )
    echo_token(info.user_name, created.token)


@token_app.command("list")
def token_list(user: UserOpt = None, as_json: JsonOpt = False) -> None:
    """Your tokens (or --user's): name, prefix, state, last use, expiry. Never values."""
    with session() as (ctx, acting):
        tokens = admin.list_tokens(ctx, acting, user)
    if as_json:
        echo_json(tokens)
        return
    rows = [["ID", "NAME", "PREFIX", "STATE", "MODE", "TOOLS", "LAST USED", "EXPIRES"]]
    for tk in tokens:
        rows.append(
            [
                tk.id,
                tk.name,
                tk.prefix or "(legacy)",
                tk.state,
                tk.tool_mode,
                str(len(tk.tools)),
                _short(tk.last_used_at),
                _short(tk.expires_at),
            ]
        )
    table(rows)


@token_app.command("revoke")
def token_revoke(token_id: str, user: UserOpt = None) -> None:
    """Revoke a token by id (yours, or --user's as an admin). Effective on its next call."""
    with session() as (ctx, acting):
        card = admin.revoke_token(ctx, acting, token_id, user)
    typer.echo(f"revoked token '{card.name}' ({card.prefix or 'legacy'}) of '{card.user_name}'")


# ── role ─────────────────────────────────────────────────────────────────────────────


@role_app.command("list")
def role_list(as_json: JsonOpt = False) -> None:
    """Roles with their allowed / on-by-default tool counts and how many users hold them."""
    with session() as (ctx, acting):
        roles = admin.list_roles(ctx, acting)
    if as_json:
        echo_json(roles)
        return
    rows = [["NAME", "ALLOWED", "DEFAULTS", "REVIEW ALL", "USERS", "BUILT-IN"]]
    for r in roles:
        rows.append(
            [
                r.name,
                str(len(r.allowed)),
                str(len(r.defaults)),
                "yes" if r.review_all else "no",
                str(r.users),
                "yes" if r.builtin else "no",
            ]
        )
    table(rows)


AllowOpt = Annotated[
    list[str] | None, typer.Option("--allow", help="Allowed tools (repeat or comma-separate)")
]
DefaultOpt = Annotated[
    list[str] | None, typer.Option("--default", help="On-by-default tools (⊆ allowed)")
]
DescriptionOpt = Annotated[str | None, typer.Option("--description", help="What it is for")]


@role_app.command("create")
def role_create(
    name: str,
    allow: AllowOpt = None,
    default: DefaultOpt = None,
    review_all: Annotated[
        bool, typer.Option("--review-all", help="May approve any proposal")
    ] = False,
    description: DescriptionOpt = None,
) -> None:
    """Create a custom role (defaults = allowed unless --default is given)."""
    with session() as (ctx, acting):
        card = admin.create_role(
            ctx,
            acting,
            name,
            description=description or "",
            allowed=_tools(allow) or [],
            defaults=_tools(default),
            review_all=review_all,
        )
    typer.echo(f"created role '{card.name}': {', '.join(card.allowed) or 'no tools'}")


@role_app.command("edit")
def role_edit(
    name: str,
    allow: AllowOpt = None,
    default: DefaultOpt = None,
    review_all: Annotated[
        bool | None, typer.Option("--review-all/--no-review-all", help="May approve anything")
    ] = None,
    description: DescriptionOpt = None,
) -> None:
    """Change a role: only the options given change."""
    with session() as (ctx, acting):
        card = admin.update_role(
            ctx,
            acting,
            name,
            description=description,
            allowed=_tools(allow),
            defaults=_tools(default),
            review_all=review_all,
        )
    typer.echo(f"role '{card.name}': allowed {', '.join(card.allowed) or 'none'}")
    typer.echo(f"  on by default: {', '.join(card.defaults) or 'none'}")


@role_app.command("delete")
def role_delete(name: str) -> None:
    """Delete a custom role that nobody holds."""
    with session() as (ctx, acting):
        admin.delete_role(ctx, acting, name)
    typer.echo(f"deleted role '{name}'")
