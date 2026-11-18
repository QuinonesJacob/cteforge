"""`db`, `user` and `settings` command groups: thin calls into services.admin."""

from __future__ import annotations

from typing import Annotated

import typer

from cteforge.cli._common import (
    app,
    context,
    echo_issues,
    echo_json,
    echo_token,
    session,
    table,
    usage,
)
from cteforge.domain.models import TeamMode
from cteforge.domain.users import ActorKind, UserRole
from cteforge.services import admin
from cteforge.services.cards import TeamSettings

db_app = typer.Typer(no_args_is_help=True, help="The cteforge database (CTEFORGE_DB_URL).")
user_app = typer.Typer(no_args_is_help=True, help="Users, roles and tokens (admin).")
settings_app = typer.Typer(no_args_is_help=True, help="Team settings (admin).")
app.add_typer(db_app, name="db")
app.add_typer(user_app, name="user")
app.add_typer(settings_app, name="settings")

JsonOpt = Annotated[bool, typer.Option("--json", help="Print JSON")]


@db_app.command("init")
def db_init() -> None:
    """Create the store and audit-log tables (idempotent)."""
    with context() as ctx:
        typer.echo(f"database ready: {ctx.config.display_db_url}")
        typer.echo(f"warehouse profiles: {', '.join(ctx.warehouses.names()) or '(none)'}")


@user_app.command("add")
def user_add(
    name: str,
    role: Annotated[
        UserRole | None, typer.Option("--role", "-r", help="viewer|contributor|approver|admin")
    ] = None,
    agent: Annotated[bool, typer.Option("--agent", help="An agent user (never verifies)")] = False,
    bootstrap: Annotated[
        bool, typer.Option("--bootstrap", help="Create the first admin (only while no users)")
    ] = False,
) -> None:
    """Create a user and print its token once."""
    kind = ActorKind.AGENT if agent else ActorKind.HUMAN
    if bootstrap:
        if role not in (None, UserRole.ADMIN):
            usage("--bootstrap creates the first admin; --role must be admin or left out")
        with context() as ctx:
            created, token = admin.bootstrap_admin(ctx, name, kind)
    else:
        if role is None:
            usage("--role is required (viewer, contributor, approver or admin)")
        with session() as (ctx, acting):
            created, token = admin.create_user(ctx, acting, name, role, kind)
    typer.echo(f"created {created.kind} user '{created.name}' with role {created.role}")
    echo_token(created.name, token)


@user_app.command("list")
def user_list(as_json: JsonOpt = False) -> None:
    """List users with their role and kind."""
    with session() as (ctx, acting):
        users = admin.list_users(ctx, acting)
    if as_json:
        echo_json(users)
        return
    rows = [["NAME", "ROLE", "KIND", "CREATED"]]
    for u in users:
        created = u.created_at.isoformat(timespec="seconds") if u.created_at else ""
        rows.append([u.name, str(u.role), str(u.kind), created])
    table(rows)


@user_app.command("role")
def user_role(name: str, role: UserRole) -> None:
    """Change a user's role."""
    with session() as (ctx, acting):
        updated = admin.set_role(ctx, acting, name, role)
    typer.echo(f"'{updated.name}' is now {updated.role}")


@user_app.command("token")
def user_token(name: str) -> None:
    """Issue a new token for NAME (the old one stops working); printed once."""
    with session() as (ctx, acting):
        token = admin.rotate_token(ctx, acting, name)
    echo_token(name, token)


@settings_app.command("show")
def settings_show(as_json: JsonOpt = False) -> None:
    """Show the team settings."""
    with session() as (ctx, acting):
        settings = admin.get_settings(ctx, acting)
    if as_json:
        echo_json(settings)
    else:
        _echo_settings(settings)


def _echo_settings(settings: TeamSettings) -> None:
    typer.echo(f"mode: {settings.mode}")
    typer.echo(f"default connection: {settings.default_connection or '(not set)'}")
    effective = settings.effective_default_connection or "(none: callers must name a profile)"
    typer.echo(f"effective default connection: {effective}")
    echo_issues(settings.issues, err=True)


@settings_app.command("mode")
def settings_mode(mode: TeamMode) -> None:
    """Set the team mode: strict (only verified usable) or open (llm_annotated too)."""
    with session() as (ctx, acting):
        settings = admin.set_settings(ctx, acting, mode)
    typer.echo(f"mode: {settings.mode}")


@settings_app.command("connection")
def settings_connection(
    name: Annotated[str | None, typer.Argument(help="Warehouse profile name")] = None,
    clear: Annotated[bool, typer.Option("--clear", help="Unset the default connection")] = False,
) -> None:
    """Set the team's default connection (the profile used when none is named; its
    dialect is the default dialect), or --clear it. Shows it when given neither."""
    if clear and name is not None:
        usage("give a NAME or --clear (not both)")
    with session() as (ctx, acting):
        if name is None and not clear:
            settings = admin.get_settings(ctx, acting)
        else:
            settings = admin.set_default_connection(ctx, acting, None if clear else name)
    _echo_settings(settings)
