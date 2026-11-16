"""`search`, `run`, `export` and `import-library`: thin calls into services.search,
services.run and services.library_io, acting as the local user (CTEFORGE_USER)."""

from __future__ import annotations

from typing import Annotated, Any

import typer

from cteforge.cli._common import (
    app,
    echo_json,
    echo_proposal_outcome,
    echo_run_outcome,
    has_error,
    parse_json_object,
    session,
    table,
    usage,
)
from cteforge.domain.dialects import Dialect
from cteforge.services.library_io import export_library, import_library
from cteforge.services.run import compile_and_run
from cteforge.services.search import search as search_library

JsonOpt = Annotated[bool, typer.Option("--json", help="Print JSON")]


@app.command()
def search(
    query: str,
    limit: Annotated[int, typer.Option("--limit", "-n")] = 10,
    drafts: Annotated[bool, typer.Option("--drafts", help="Include drafts")] = False,
    as_json: JsonOpt = False,
) -> None:
    """Search the library (title, keywords, meaning, variables, outputs, tables)."""
    with session() as (ctx, user):
        hits = search_library(ctx, user, query, limit=limit, include_drafts=drafts)
    if as_json:
        echo_json(hits)
        return
    if not hits:
        typer.echo("no matches")
        return
    rows = [["ID", "VERSION", "STATE", "SCORE", "TITLE", "VARIABLES"]]
    for h in hits:
        variables = ", ".join(f"{v.name}:{v.type}" for v in h.variables)
        rows.append([h.id, f"v{h.version}", h.state, f"{h.score:.3f}", h.title, variables])
    table(rows)


def _parse_use(raw: str) -> tuple[str, dict[str, Any]]:
    """`alias=id` or `alias=id@version` → (alias, {"id", "version"?})."""
    alias, sep, ref = raw.partition("=")
    if not sep or not alias.strip() or not ref.strip():
        usage(f"--use must look like alias=definition_id[@version], got {raw!r}")
    id_, at, version = ref.strip().partition("@")
    spec: dict[str, Any] = {"id": id_}
    if at:
        if not version.isdigit():
            usage(f"--use version must be an integer, got {version!r}")
        spec["version"] = int(version)
    return alias.strip(), spec


@app.command()
def run(
    use: Annotated[
        list[str],
        typer.Option("--use", "-u", help="alias=definition_id[@version] (repeatable)"),
    ],
    values: Annotated[
        str,
        typer.Option("--vars", help='JSON: alias → variable → value, e.g. {"rr": {"x": 1}}'),
    ] = "{}",
    select: Annotated[
        str | None, typer.Option("--select", help="Final SELECT over the aliases")
    ] = None,
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    dialect: Annotated[Dialect | None, typer.Option("--dialect", "-d")] = None,
    limit: Annotated[int, typer.Option("--limit", "-n")] = 100,
    compile_only: Annotated[
        bool, typer.Option("--compile-only", help="Compile and label; do not run")
    ] = False,
    show_sql: Annotated[bool, typer.Option("--sql", help="Print the compiled SQL")] = False,
    question: Annotated[
        str | None, typer.Option("--question", "-q", help="Recorded in the audit log")
    ] = None,
    as_json: JsonOpt = False,
) -> None:
    """Compile definitions by reference (+ optional final SELECT), label, check, run.
    Exit 1 when the run is refused or has error issues."""
    uses: dict[str, dict[str, Any]] = {}
    for raw in use:
        alias, spec = _parse_use(raw)
        if alias in uses:
            usage(f"--use alias {alias!r} given twice")
        uses[alias] = spec
    parsed = parse_json_object(values, "--vars")
    with session() as (ctx, user):
        outcome = compile_and_run(
            ctx,
            user,
            uses=uses,
            values=parsed,
            select=select,
            dialect=dialect,
            profile=profile,
            run=not compile_only,
            limit=limit,
            question=question,
        )
    if as_json:
        echo_json(outcome)
    else:
        echo_run_outcome(outcome, show_sql=show_sql)
    if not outcome.usable or has_error(outcome.issues):
        raise typer.Exit(1)


@app.command("export")
def export(
    directory: str,
    prune: Annotated[
        bool, typer.Option("--prune", help="Remove other *.yml files so DIR mirrors the library")
    ] = False,
) -> None:
    """Write the library as YAML files (definitions/, recipes/, joins/) into DIRECTORY."""
    with session() as (ctx, user):
        written = export_library(ctx, user, directory, prune=prune)
    for path in written:
        typer.echo(f"wrote {path}")
    typer.echo(f"{len(written)} file(s) exported to {directory}")


@app.command("import-library")
def import_library_(
    directory: str,
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    as_json: JsonOpt = False,
) -> None:
    """Turn every changed file in a library DIRECTORY into a proposal (normal review rules)."""
    with session() as (ctx, user):
        outcomes = import_library(ctx, user, directory, profile=profile)
    if as_json:
        echo_json(outcomes)
        return
    if not outcomes:
        typer.echo("nothing changed")
    for outcome in outcomes:
        echo_proposal_outcome(outcome)
