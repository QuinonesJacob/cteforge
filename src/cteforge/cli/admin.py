"""`db`, `user` and `settings` command groups: thin calls into services.admin.
(Tokens and roles: cli/access.py.)"""

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
user_app = typer.Typer(no_args_is_help=True, help="Users (admin).")
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
        str | None,
        typer.Option("--role", "-r", help="viewer|contributor|approver|admin or a custom role"),
    ] = None,
    agent: Annotated[
        bool, typer.Option("--agent", help="An agent user (verifies only as admin)")
    ] = False,
    bootstrap: Annotated[
        bool, typer.Option("--bootstrap", help="Create the first admin (only while no users)")
    ] = False,
) -> None:
    """Create a user and print its "default" token once."""
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
    if token is not None:
        echo_token(created.name, token)


@user_app.command("list")
def user_list(as_json: JsonOpt = False) -> None:
    """List users with their role, kind, active flag and active tokens."""
    with session() as (ctx, acting):
        users = admin.list_users(ctx, acting)
    if as_json:
        echo_json(users)
        return
    rows = [["NAME", "ROLE", "KIND", "ACTIVE", "TOKENS", "CREATED"]]
    for u in users:
        created = u.created_at.isoformat(timespec="seconds") if u.created_at else ""
        live = sum(1 for tk in u.tokens if tk.state == "active")
        rows.append([u.name, u.role, str(u.kind), "yes" if u.active else "no", str(live), created])
    table(rows)


@user_app.command("role")
def user_role(name: str, role: str) -> None:
    """Change a user's role (a built-in or custom role name)."""
    with session() as (ctx, acting):
        updated = admin.set_role(ctx, acting, name, role)
    typer.echo(f"'{updated.name}' is now {updated.role}")


@user_app.command("deactivate")
def user_deactivate(name: str) -> None:
    """Deactivate a user: none of their tokens work until they are activated again."""
    with session() as (ctx, acting):
        admin.set_active(ctx, acting, name, False)
    typer.echo(f"'{name}' is deactivated")


@user_app.command("activate")
def user_activate(name: str) -> None:
    """Activate a deactivated user (their unrevoked, unexpired tokens work again)."""
    with session() as (ctx, acting):
        admin.set_active(ctx, acting, name, True)
    typer.echo(f"'{name}' is active")


@user_app.command("profile")
def user_profile(
    name: str,
    display_name: Annotated[
        str | None, typer.Option("--display-name", help='Display name ("" clears it)')
    ] = None,
    email: Annotated[str | None, typer.Option("--email", help='Email ("" clears it)')] = None,
) -> None:
    """Set a user's display name and/or email (ADR 0029)."""
    if display_name is None and email is None:
        usage("give --display-name and/or --email")
    with session() as (ctx, acting):
        view = admin.update_profile(
            ctx,
            acting,
            name,
            display_name=admin.KEEP if display_name is None else display_name,
            email=admin.KEEP if email is None else email,
        )
    shown_name = view.display_name or "(none)"
    typer.echo(f"'{view.name}': display name {shown_name}, email {view.email or '(none)'}")


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
