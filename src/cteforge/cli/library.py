"""`search`, `search-index`, `run`, `references`, `rename`, `export` and `import-library`:
thin calls into services.search / search_index, services.run, services.describe /
services.propose (ADR 0024) and services.library_io, acting as the local user
(CTEFORGE_USER)."""

from __future__ import annotations

from typing import Annotated, Any

import typer

from cteforge.cli._common import (
    app,
    echo_json,
    echo_proposal_outcome,
    echo_run_outcome,
    errors,
    has_error,
    parse_json_object,
    session,
    table,
    usage,
    warn,
)
from cteforge.domain.dialects import Dialect
from cteforge.services import describe, propose, search_index
from cteforge.services.context import AppConfig
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
    """Search the library by words and by meaning (title, keywords, meaning, variables,
    outputs, tables). The first search may download the embedding model (~65 MB, once)."""
    with session() as (ctx, user):
        hits = search_library(ctx, user, query, limit=limit, include_drafts=drafts)
    if as_json:
        echo_json(hits)
        return
    notes = dict.fromkeys(h.note for h in hits if h.note)
    for note in notes:
        warn(f"note: {note}")
    if not hits:
        typer.echo("no matches")
        return
    rows = [["ID", "VERSION", "STATE", "MATCH", "SCORE", "TITLE", "VARIABLES"]]
    for h in hits:
        variables = ", ".join(f"{v.name}:{v.type}" for v in h.variables)
        match = h.match or "-"
        rows.append([h.id, f"v{h.version}", h.state, match, f"{h.score:.3f}", h.title, variables])
    table(rows)


search_index_app = typer.Typer(
    no_args_is_help=True, help="The meaning-search index (embedding model and vectors)."
)
app.add_typer(search_index_app, name="search-index")


@search_index_app.command("rebuild")
def search_index_rebuild(
    force: Annotated[
        bool, typer.Option("--force", help="Delete every stored vector and embed all again")
    ] = False,
    as_json: JsonOpt = False,
) -> None:
    """Embed every searchable definition whose vector is missing or out of date (admin)."""
    with session() as (ctx, user):
        report = search_index.rebuild_search_index(ctx, user, force=force)
    if as_json:
        echo_json(report)
        return
    typer.echo(
        f"{report.model}: {report.definitions} versions; {report.embedded} embedded, "
        f"{report.unchanged} unchanged, {report.removed} old vectors removed"
    )


@search_index_app.command("download-model")
def search_index_download_model() -> None:
    """Download the embedding model into CTEFORGE_EMBED_CACHE (for offline servers)."""
    with errors():
        config = AppConfig.from_env()
        path = search_index.download_model(config)
    typer.echo(f"embedding model ready in {path}")


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
    """Write the library as YAML files (definitions/, joins/) into DIRECTORY."""
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


@app.command()
def references(id: str, as_json: JsonOpt = False) -> None:
    """What inside cteforge uses definition ID (composed definitions built on it, pending
    proposals, former IDs): what a rename would update (ADR 0024, 0026)."""
    with session() as (ctx, user):
        refs = describe.definition_references(ctx, user, id)
    if as_json:
        echo_json(refs)
        return
    typer.echo(f"{refs.id} v{refs.version} ({refs.state})")
    if refs.used_by:
        rows = [["USED BY", "VERSION", "ALIAS", "USES"]]
        for r in refs.used_by:
            uses = f"v{r.pinned_version} (pinned)" if r.pinned_version else "latest"
            rows.append([r.definition_id, f"v{r.definition_version}", r.alias, uses])
        typer.echo("")
        table(rows)
    else:
        typer.echo("no composed definitions use it")
    if refs.proposals:
        rows = [["PROPOSAL", "KIND", "RELATION", "AUTHOR", "SUMMARY"]]
        for p in refs.proposals:
            rows.append([p.proposal_id, str(p.kind), p.relation, p.author, p.summary])
        typer.echo("")
        table(rows)
    else:
        typer.echo("no pending proposals name it")
    for f in refs.former_ids:
        when = f.renamed_at.date().isoformat()
        typer.echo(
            f"formerly {f.former_id} (renamed to {f.renamed_to} by {f.renamed_by} on {when})"
        )
    typer.echo(f"\nnote: {refs.outside_warning}")


@app.command()
def rename(
    old: str,
    new: str,
    summary: Annotated[str, typer.Option("--summary", "-m", help="Why (for reviewers)")] = "",
    as_json: JsonOpt = False,
) -> None:
    """Propose renaming definition OLD to NEW (ADR 0024). Applied at once in open mode when
    OLD is not verified; otherwise it waits for approval. Run `cteforge references OLD`
    first: references outside cteforge stop working."""
    with session() as (ctx, user):
        outcome = propose.rename_definition(ctx, user, id=old, new_id=new, summary=summary)
    if as_json:
        echo_json(outcome)
    else:
        echo_proposal_outcome(outcome)
        if not outcome.auto_applied:
            typer.echo("  waits for approval: cteforge proposals approve " + outcome.proposal.id)
