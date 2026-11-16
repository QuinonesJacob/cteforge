"""CLI: thin output over services; clean errors (exit 2 for bad input)."""

from pathlib import Path

from typer.testing import CliRunner

from cteforge.cli.main import app

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
RR_SQL = str(EXAMPLES / "queries" / "realization_rate.sql")
RR_DEF = str(EXAMPLES / "definitions" / "realization_rate.yml")
runner = CliRunner()


def test_vars_must_be_a_json_object():
    for bad in ("[1, 2]", '"x"', "{nope"):
        result = runner.invoke(app, ["render", RR_DEF, "--vars", bad])
        assert result.exit_code == 2, bad
        assert "--vars" in result.output and "Traceback" not in result.output


def test_parse_shows_facts_ranges_and_joins(warehouse_path):
    sql = str(EXAMPLES / "queries" / "booked_vs_collected.sql")
    result = runner.invoke(app, ["parse", sql, "-w", str(warehouse_path)])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "wrapper: STRPTIME(?, '%m-%d-%Y')" in out and "format: %m-%d-%Y" in out
    assert "context: value_wrapped" in out
    assert "DECIMAL(12, 2)" in out  # compared type of a derived CTE column
    assert "[booked.L1, booked.L2] on d.closed_at: half_open" in out
    assert "main: booked left join collected on booked.region = collected.region" in out


def test_import_prints_issues_with_code_and_severity(tmp_path):
    out_file = tmp_path / "rr.yml"
    annotations = str(EXAMPLES / "definitions" / "realization_rate.annotations.yml")
    result = runner.invoke(app, ["import", RR_SQL, annotations, "-o", str(out_file)])
    assert result.exit_code == 0, result.output
    assert "state: draft" in result.output
    assert "warning: checks_incomplete: No warehouse" in result.output
    assert "warning: type_unknown [period] [closed_deals.L1]" in result.output
    assert out_file.exists()


def test_render_runs_the_demo(warehouse_path):
    values = '{"period": {"start": "2025-07-01", "end": "2025-10-01"}, "regions": ["APAC"]}'
    result = runner.invoke(
        app, ["render", RR_DEF, "--vars", values, "-w", str(warehouse_path), "--run"]
    )
    assert result.exit_code == 0, result.output
    assert "month | region | realization_rate" in result.output
    assert "| APAC |" in result.output
