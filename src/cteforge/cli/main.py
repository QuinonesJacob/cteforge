"""cteforge CLI — thin adapter over services/. No logic beyond parsing args and printing.

Commands live in sibling modules that register on the shared app (`_common.app`):
admin (db/user/settings), access (token/role), review (proposals/verify/revalidate),
library (search/run/export/import-library), server (serve/mcp), demo. This module keeps
the M1 file-based commands (parse/import/render) and is the console-script entry point.
"""

from __future__ import annotations

from typing import Annotated, Any

import typer

from cteforge.cli import access, admin, demo, library, review, server  # noqa: F401  (register)
from cteforge.cli._common import (
    app,
    echo_issues,
    fail,
    has_error,
    parse_json_object,
    table,
)
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import CteforgeError
from cteforge.services.connections import open_warehouse
from cteforge.services.import_query import analyze_file, import_files
from cteforge.services.render_definition import render_file

DialectOpt = Annotated[Dialect, typer.Option("--dialect", "-d", help="Source SQL dialect")]
WarehouseOpt = Annotated[
    str | None, typer.Option("--warehouse", "-w", help="DuckDB file for types and runs")
]


def _warehouse(path: str | None) -> Any:
    return open_warehouse(path) if path else (None, None)


def _literal_notes(lit: Any) -> str:
    notes = [
        f"typed {lit.cast_type}" if lit.cast_type else "",
        f"in {lit.in_list}" if lit.in_list else "",
        f"column side: {lit.column_sql}" if lit.wrapped else "",
        f"wrapper: {lit.wrapper_sql}" if lit.wrapper_sql else "",
        f"format: {lit.suggested_format}" if lit.suggested_format else "",
        f"context: {', '.join(lit.context)}" if lit.context else "",
    ]
    return " · ".join(n for n in notes if n)


def _print_analysis(analysis: Any) -> None:
    typer.echo(f"tables:  {', '.join(analysis.tables)}")
    typer.echo(f"ctes:    {', '.join(c.name for c in analysis.ctes)}")
    typer.echo(f"outputs: {', '.join(analysis.outputs)}\n")
    rows = [["ID", "OP", "VALUE", "FILTERS", "COMPARED TYPE", "NOTES"]]
    for lit in analysis.literals:
        col = lit.column
        target = f"{col.table}.{col.column}" if col and col.table else (lit.column_sql or "?")
        compared = lit.compared_type or (col.type if col and col.type else "-")
        rows.append([lit.id, lit.operator, lit.value, target, compared, _literal_notes(lit)])
    table(rows)
    if analysis.ranges:
        typer.echo("\nrange candidates:")
        for r in analysis.ranges:
            typer.echo(f"  [{r.start}, {r.end}] on {r.column_sql}: {r.shape}")
    if analysis.joins:
        typer.echo("\njoins:")
        for j in analysis.joins:
            keys = " AND ".join(f"{j.left_table}.{a} = {j.right_table}.{b}" for a, b in j.keys)
            typer.echo(
                f"  {j.scope}: {j.left_table} {j.join_type} join {j.right_table}"
                + (f" on {keys}" if keys else "")
            )


@app.command()
def parse(
    sql_file: str,
    dialect: DialectOpt = Dialect.DUCKDB,
    warehouse: WarehouseOpt = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List the filter literals in a trusted query: IDs, columns, types, structural facts."""
    try:
        wh, _ = _warehouse(warehouse)
        analysis = analyze_file(sql_file, dialect, wh)
    except CteforgeError as exc:
        fail(exc)
    if as_json:
        typer.echo(analysis.model_dump_json(indent=2))
    else:
        _print_analysis(analysis)


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
        fail(exc)
    ok = typer.style("identical", fg=typer.colors.GREEN)
    typer.echo(f"same query back (SQL):     {ok if outcome.roundtrip.identical else 'DIFFERENT'}")
    if outcome.results_match is not None:
        rm = "identical" if outcome.results_match else "DIFFERENT"
        typer.echo(f"same query back (results): {rm}")
    typer.echo(f"state: {outcome.definition.state}")
    echo_issues(outcome.issues)
    if outcome.roundtrip.diff:
        typer.echo(outcome.roundtrip.diff)
    if has_error(outcome.issues):
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
    parsed = parse_json_object(values, "--vars")
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
        fail(exc)
    echo_issues(outcome.render.issues, err=True)
    typer.echo(outcome.render.sql)
    if outcome.result is not None:
        typer.echo("\n" + " | ".join(outcome.result.columns))
        for row in outcome.result.rows:
            typer.echo(" | ".join(str(v) for v in row))
        if outcome.result.truncated:
            typer.echo(f"... (limited to {limit} rows)")


if __name__ == "__main__":
    app()
