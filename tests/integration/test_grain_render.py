"""grain variable on a real DuckDB warehouse (M1.5): import the example, render quarter."""

from pathlib import Path

from cteforge.domain.codec import annotations_from_yaml
from cteforge.domain.dialects import Dialect
from cteforge.domain.issues import has_errors
from cteforge.domain.models import DefinitionState
from cteforge.services.import_query import import_query
from cteforge.templating.render import render

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
YEAR = {"start": "2026-01-01", "end": "2027-01-01"}


def _import(sample_sql, warehouse):
    text = (EXAMPLES / "definitions" / "realization_rate_grain.annotations.yml").read_text()
    return import_query(sample_sql, Dialect.DUCKDB, annotations_from_yaml(text), warehouse)


def test_grain_example_imports_with_identical_results(sample_sql, warehouse):
    outcome = _import(sample_sql, warehouse)
    assert outcome.roundtrip.identical, outcome.roundtrip.diff
    assert outcome.results_match is True
    assert not has_errors(outcome.issues)
    assert outcome.definition.state is DefinitionState.LLM_ANNOTATED


def test_month_to_quarter_returns_quarterly_rows(sample_sql, warehouse):
    definition = _import(sample_sql, warehouse).definition
    monthly = warehouse.run(render(definition, {"period": YEAR}).sql, 10_000)
    quarterly = warehouse.run(
        render(definition, {"period": YEAR, "time_grain": "quarter"}).sql, 10_000
    )
    assert quarterly.columns == ["month", "region", "realization_rate"]
    periods = {row[0] for row in quarterly.rows}
    assert periods and all(p.month in (1, 4, 7, 10) and p.day == 1 for p in periods)
    assert len(periods) < len({row[0] for row in monthly.rows})
    # Same regions, one row per quarter per region.
    assert len(quarterly.rows) == len({(row[0], row[1]) for row in quarterly.rows})


def test_week_truncates_to_monday_in_duckdb(sample_sql, warehouse):
    definition = _import(sample_sql, warehouse).definition
    weekly = warehouse.run(render(definition, {"period": YEAR, "time_grain": "week"}).sql, 10_000)
    assert weekly.rows and all(row[0].weekday() == 0 for row in weekly.rows)  # Monday
