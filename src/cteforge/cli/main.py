"""cteforge CLI — thin adapter over services/. No logic beyond parsing args and printing."""

from __future__ import annotations

import json
from typing import Annotated, Any

import typer

from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import CteforgeError
from cteforge.services.connections import open_warehouse
from cteforge.services.import_query import analyze_file, import_files
from cteforge.services.render_definition import render_file

app = typer.Typer(no_args_is_help=True, add_completion=False, help=__doc__)

DialectOpt = Annotated[Dialect, typer.Option("--dialect", "-d", help="Source SQL dialect")]
WarehouseOpt = Annotated[
    str | None, typer.Option("--warehouse", "-w", help="DuckDB file for types and runs")
]


def _fail(exc: CteforgeError) -> None:
    typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


def _warehouse(path: str | None) -> Any:
    return open_warehouse(path) if path else (None, None)


@app.command()
def parse(
    sql_file: str,
    dialect: DialectOpt = Dialect.DUCKDB,
    warehouse: WarehouseOpt = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List the filter literals in a trusted query, with the columns they filter."""
    try:
        wh, _ = _warehouse(warehouse)
        analysis = analyze_file(sql_file, dialect, wh)
    except CteforgeError as exc:
        _fail(exc)
    if as_json:
        typer.echo(analysis.model_dump_json(indent=2))
        return
    typer.echo(f"tables: {', '.join(analysis.tables)}")
    typer.echo(f"ctes:   {', '.join(c.name for c in analysis.ctes)}")
    typer.echo(f"outputs: {', '.join(analysis.outputs)}\n")
    typer.echo(f"{'ID':<22}{'OP':<13}{'VALUE':<14}{'FILTERS':<28}{'TYPE':<12}NOTE")
    for lit in analysis.literals:
        col = lit.column
        target = f"{col.table}.{col.column}" if col and col.table else (lit.column_sql or "?")
        notes = [
            n
            for n in (
                lit.in_list,
                "wrapped: " + (lit.column_sql or "") if lit.wrapped else None,
                f"typed {lit.cast_type}" if lit.cast_type else None,
            )
            if n
        ]
        typer.echo(
            f"{lit.id:<22}{lit.operator:<13}{lit.value:<14}{target:<28}"
            f"{(col.type if col and col.type else '-'):<12}{'; '.join(notes)}"
        )
    for r in analysis.ranges:
        typer.echo(f"\nrange candidate: {r.column_sql} [{r.start}, {r.end}] ({r.shape})")


@app.command("import")
def import_(
    sql_file: str,
    annotations: str,
    dialect: DialectOpt = Dialect.DUCKDB,
    warehouse: WarehouseOpt = None,
    out: Annotated[str | None, typer.Option("--out", "-o", help="Write definition YAML")] = None,
) -> None:
    """Turn a trusted query + annotations into a definition, checking nothing changed."""
    try:
        wh, _ = _warehouse(warehouse)
        outcome = import_files(sql_file, annotations, dialect, wh, out)
    except CteforgeError as exc:
        _fail(exc)
    ok = typer.style("identical", fg=typer.colors.GREEN)
    typer.echo(f"same query back (SQL):     {ok if outcome.roundtrip.identical else 'DIFFERENT'}")
    if outcome.results_match is not None:
        rm = "identical" if outcome.results_match else "DIFFERENT"
        typer.echo(f"same query back (results): {rm}")
    typer.echo(f"state: {outcome.definition.state}")
    for problem in outcome.problems:
        typer.secho(f"problem: {problem}", fg=typer.colors.RED)
    if outcome.roundtrip.diff:
        typer.echo(outcome.roundtrip.diff)
    if outcome.problems:
        raise typer.Exit(1)
    if out:
        typer.echo(f"wrote {out}")
    else:
        typer.echo("\n" + outcome.definition.template)


@app.command()
def render(
    definition: str,
    values: Annotated[str, typer.Option("--vars", help="JSON object of values")] = "{}",
    dialect: Annotated[
        Dialect | None, typer.Option("--dialect", "-d", help="Target dialect")
    ] = None,
    warehouse: WarehouseOpt = None,
    run: Annotated[bool, typer.Option("--run", help="Check and run on the warehouse")] = False,
    limit: int = 20,
) -> None:
    """Render a definition with new variable values (and optionally run it)."""
    try:
        parsed = json.loads(values)
    except json.JSONDecodeError as exc:
        typer.secho(f"error: --vars is not valid JSON: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    try:
        wh, wh_dialect = _warehouse(warehouse)
        outcome = render_file(
            definition,
            parsed,
            dialect=dialect,
            warehouse=wh,
            warehouse_dialect=wh_dialect,
            run=run,
            limit=limit,
        )
    except CteforgeError as exc:
        _fail(exc)
    for warning in outcome.render.warnings:
        typer.secho(f"warning: {warning}", fg=typer.colors.YELLOW, err=True)
    typer.echo(outcome.render.sql)
    if outcome.result is not None:
        typer.echo("\n" + " | ".join(outcome.result.columns))
        for row in outcome.result.rows:
            typer.echo(" | ".join(str(v) for v in row))
        if outcome.result.truncated:
            typer.echo(f"... (limited to {limit} rows)")


if __name__ == "__main__":
    app()
