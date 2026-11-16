"""`proposals` group, `verify` and `revalidate`: thin calls into services.review and
services.revalidate. Approving and verifying are human acts (ADR 0007, 0018): the CLI acts
as the local user (CTEFORGE_USER)."""

from __future__ import annotations

from typing import Annotated

import typer

from cteforge.cli._common import (
    app,
    echo_issues,
    echo_json,
    echo_proposal_outcome,
    echo_proposal_view,
    session,
    table,
    usage,
)
from cteforge.domain.proposals import ProposalStatus
from cteforge.services import review
from cteforge.services.revalidate import revalidate as revalidate_library

proposals_app = typer.Typer(no_args_is_help=True, help="Review proposals (DESIGN §18).")
app.add_typer(proposals_app, name="proposals")

JsonOpt = Annotated[bool, typer.Option("--json", help="Print JSON")]


@proposals_app.command("list")
def proposals_list(
    status: Annotated[
        ProposalStatus | None, typer.Option("--status", "-s", help="Only this status")
    ] = None,
    as_json: JsonOpt = False,
) -> None:
    """Proposals, newest first."""
    with session() as (ctx, user):
        views = review.list_proposals(ctx, user, status)
    if as_json:
        echo_json(views)
        return
    if not views:
        typer.echo("no proposals")
        return
    rows = [["ID", "KIND", "TARGET", "STATUS", "AUTHOR", "CREATED", "SUMMARY"]]
    for v in views:
        status_text = f"{v.status}{' (auto)' if v.auto_applied else ''}"
        created = v.created_at.isoformat(timespec="minutes")
        rows.append([v.id, v.kind, v.target_id, status_text, v.author, created, v.summary])
    table(rows)


@proposals_app.command("show")
def proposals_show(id: str, as_json: JsonOpt = False) -> None:
    """One proposal: checks, impact and the diff against its base."""
    with session() as (ctx, user):
        view = review.get_proposal(ctx, user, id)
    if as_json:
        echo_json(view)
    else:
        echo_proposal_view(view)


@proposals_app.command("approve")
def proposals_approve(
    id: str,
    verify: Annotated[
        bool, typer.Option("--verify", help="Also mark the result verified (human only)")
    ] = False,
    comment: Annotated[str, typer.Option("--comment", "-m")] = "",
    as_json: JsonOpt = False,
) -> None:
    """Apply a pending proposal."""
    with session() as (ctx, user):
        outcome = review.approve(ctx, user, id, verify=verify, comment=comment)
    if as_json:
        echo_json(outcome)
    else:
        echo_proposal_outcome(outcome)


@proposals_app.command("reject")
def proposals_reject(
    id: str,
    comment: Annotated[str, typer.Option("--comment", "-m", help="Why (required)")] = "",
) -> None:
    """Reject a pending proposal; the comment is kept on it."""
    if not comment.strip():
        usage("--comment is required: say why the proposal is rejected")
    with session() as (ctx, user):
        view = review.reject(ctx, user, id, comment)
    typer.echo(f"proposal {view.id}: {view.status}")


@app.command()
def verify(id: str, version: int, as_json: JsonOpt = False) -> None:
    """Mark the latest VERSION of definition ID verified by you (human approver/admin)."""
    with session() as (ctx, user):
        card = review.verify_definition(ctx, user, id, version)
    if as_json:
        echo_json(card)
    else:
        typer.echo(f"{card.id} v{card.version}: {card.state} by {card.verified_by}")


@app.command()
def revalidate(
    ids: Annotated[list[str] | None, typer.Argument(help="Definition ids (default: all)")] = None,
    as_json: JsonOpt = False,
) -> None:
    """Re-run the checks of the latest versions against their warehouse; report drift.
    Exit 1 when any definition drifted."""
    with session() as (ctx, user):
        report = revalidate_library(ctx, user, list(ids) if ids else None)
    if as_json:
        echo_json(report)
    else:
        rows = [["ID", "VERSION", "STATE", "PROFILE", "CHECKED", "RESULT"]]
        for r in report:
            result = "ok" if r.ok else "DRIFT"
            rows.append(
                [r.id, str(r.version), r.state, r.profile or "-", str(r.checked).lower(), result]
            )
        table(rows)
        for r in report:
            if r.issues:
                typer.echo(f"\n{r.id}:")
                echo_issues(r.issues)
    if any(not r.ok for r in report):
        raise typer.Exit(1)
