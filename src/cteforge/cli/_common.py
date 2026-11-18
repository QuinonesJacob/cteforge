"""Shared CLI plumbing: the Typer app, opening a context as the local user, error exits,
and printing helpers. No business logic: argument parsing and output formatting only."""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from decimal import Decimal
from typing import Any, NoReturn

import typer
from pydantic import BaseModel

from cteforge.domain.errors import CteforgeError
from cteforge.domain.issues import Issue, Severity
from cteforge.domain.proposals import ProposalKind
from cteforge.domain.users import User
from cteforge.services._audit import surface as audit_surface
from cteforge.services.auth import local_user
from cteforge.services.cards import ProposalOutcome, ProposalView, RunOutcome
from cteforge.services.context import AppConfig, AppContext, open_context

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="cteforge: a library of trusted SQL for agents (CLI, local admin tool).",
)

USAGE_ERROR = 2
_COLORS = {Severity.ERROR: typer.colors.RED, Severity.WARNING: typer.colors.YELLOW}


# ── Exits ────────────────────────────────────────────────────────────────────────────


def fail(exc: CteforgeError | str) -> NoReturn:
    """A cteforge error (or refusal): message to stderr, exit 1."""
    typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
    for issue in getattr(exc, "issues", None) or []:
        typer.secho(issue_line(issue), fg=_COLORS.get(issue.severity), err=True)
    raise typer.Exit(1)


def usage(message: str) -> NoReturn:
    """Bad command-line input: message to stderr, exit 2."""
    typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(USAGE_ERROR)


def warn(message: str) -> None:
    typer.secho(message, fg=typer.colors.YELLOW, err=True)


# ── Context ──────────────────────────────────────────────────────────────────────────


@contextmanager
def errors() -> Iterator[None]:
    """Turn a CteforgeError raised inside the block into `fail` (exit 1)."""
    try:
        yield
    except CteforgeError as exc:
        fail(exc)


@contextmanager
def context(config: AppConfig | None = None) -> Iterator[AppContext]:
    """Open the app context from the environment (CTEFORGE_*); errors exit 1. Calls made
    inside are from the CLI surface (ADR 0029: successful reads are not logged)."""
    with errors():
        ctx = open_context(config or AppConfig.from_env())
    try:
        with errors(), audit_surface("cli"):
            yield ctx
    finally:
        ctx.close()


@contextmanager
def session() -> Iterator[tuple[AppContext, User]]:
    """Context plus the acting local user (CTEFORGE_USER; services.auth.local_user)."""
    with context() as ctx:
        with errors():
            user = local_user(ctx)
        yield ctx, user


# ── Input ────────────────────────────────────────────────────────────────────────────


def parse_json_object(raw: str, option: str) -> dict[str, Any]:
    """A JSON object from the command line; decimals kept exact (1000.50). Exit 2 if not."""
    try:
        parsed = json.loads(raw, parse_float=Decimal)
    except json.JSONDecodeError as exc:
        usage(f"{option} is not valid JSON: {exc}")
    if not isinstance(parsed, dict):
        usage(f'{option} must be a JSON object like {{"name": value}}, got {type(parsed).__name__}')
    return parsed


# ── Output ───────────────────────────────────────────────────────────────────────────


def issue_line(issue: Issue) -> str:
    where = " ".join(f"[{x}]" for x in (issue.variable, issue.literal) if x)
    return f"{issue.severity}: {issue.code}{' ' + where if where else ''}: {issue.message}"


def echo_issues(issues: Sequence[Issue], *, err: bool = False) -> None:
    for issue in issues:
        typer.secho(issue_line(issue), fg=_COLORS.get(issue.severity), err=err)


def has_error(issues: Sequence[Issue]) -> bool:
    return any(i.severity is Severity.ERROR for i in issues)


def table(rows: list[list[str]]) -> None:
    """Left-aligned columns; the first row is the header."""
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    for row in rows:
        typer.echo("  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)).rstrip())


def echo_json(data: BaseModel | Sequence[BaseModel] | Any) -> None:
    if isinstance(data, BaseModel):
        typer.echo(data.model_dump_json(indent=2))
        return
    if isinstance(data, (list, tuple)):
        data = [d.model_dump(mode="json") if isinstance(d, BaseModel) else d for d in data]
    typer.echo(json.dumps(data, indent=2, default=str))


def echo_token(who: str, token: str) -> None:
    """A token is shown exactly once: the token on stdout, the warning on stderr."""
    warn(f"Token for '{who}' (shown ONCE; it is stored only as a hash; keep it secret):")
    typer.echo(token)


def cell(value: Any) -> str:
    return "" if value is None else str(value)


def echo_rows(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
    if not columns:
        return
    table([list(columns), *[[cell(v) for v in row] for row in rows]])


def echo_proposal_outcome(outcome: ProposalOutcome) -> None:
    p = outcome.proposal
    applied = " (auto-applied)" if outcome.auto_applied else ""
    typer.echo(f"proposal {p.id}: {p.kind} {p.target_id} — {p.status}{applied}")
    if outcome.definition is not None:
        d = outcome.definition
        typer.echo(f"  definition {d.id} v{d.version}: {d.state}")
    echo_issues(outcome.issues or p.checks)


def echo_proposal_view(view: ProposalView) -> None:
    rows = [
        ["id", view.id],
        ["kind", str(view.kind)],
        ["target", view.target_id + (f" (base v{view.base_version})" if view.base_version else "")],
        ["status", str(view.status) + (" (auto-applied)" if view.auto_applied else "")],
        ["author", f"{view.author} ({view.author_kind})"],
        ["created", view.created_at.isoformat(timespec="seconds")],
    ]
    if view.summary:
        rows.append(["summary", view.summary])
    if view.decided_by:
        decided_at = view.decided_at.isoformat(timespec="seconds") if view.decided_at else ""
        rows.append(["decided", f"{view.decided_by} {decided_at}".strip()])
    if view.comment:
        rows.append(["comment", view.comment])
    if view.resulting_version is not None:
        rows.append(["version", f"v{view.resulting_version}"])
    if view.impact:
        rows.append(["impact", ", ".join(view.impact)])
    if view.kind is ProposalKind.RENAME_DEFINITION:
        rows.append(["rename", f"{view.payload.get('from_id')} -> {view.payload.get('to_id')}"])
    table(rows)
    if view.checks:
        typer.echo("\nchecks:")
        echo_issues(view.checks)
    if view.diff:
        typer.echo("\n" + view.diff.rstrip("\n"))


def echo_run_outcome(outcome: RunOutcome, *, show_sql: bool) -> None:
    usable = "" if outcome.usable else " (REFUSED)"
    typer.echo(f"label: {outcome.label}{usable}")
    for reason in outcome.reasons:
        typer.echo(f"  - {reason}")
    for used in outcome.used:
        by = f", verified by {used.verified_by} on {used.verified_at}" if used.verified_by else ""
        typer.echo(f"used: {used.id}@{used.version} ({used.state}{by})")
    echo_issues(outcome.issues, err=True)
    if show_sql or not outcome.ran:
        typer.echo("\n" + outcome.sql)
    if outcome.ran:
        typer.echo("")
        echo_rows(outcome.columns, outcome.rows)
        more = " (truncated)" if outcome.truncated else ""
        timing = f" in {outcome.duration_ms} ms" if outcome.duration_ms is not None else ""
        typer.echo(f"\n{outcome.row_count} row(s){more}{timing}")
