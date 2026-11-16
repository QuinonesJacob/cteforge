from cteforge.domain.dialects import Dialect
from cteforge.domain.models import DefinitionState
from cteforge.files.definitions import load_definition, save_definition
from cteforge.services.import_query import import_query
from cteforge.services.render_definition import render_definition

D = Dialect.DUCKDB


def test_static_schema_matches_warehouse(warehouse, sample_schema):
    for table, columns in sample_schema.items():
        real = {c: t.replace(" ", "") for c, t in warehouse.column_types(table).items()}
        assert real == columns


def test_import_proves_same_results(sample_sql, sample_annotations, warehouse):
    outcome = import_query(sample_sql, D, sample_annotations, warehouse)
    assert outcome.problems == []
    assert outcome.roundtrip.identical
    assert outcome.results_match is True
    assert outcome.definition.state is DefinitionState.LLM_ANNOTATED  # never auto-verified


def test_render_and_run_new_values(sample_sql, sample_annotations, warehouse, tmp_path):
    definition = import_query(sample_sql, D, sample_annotations, warehouse).definition
    path = tmp_path / "rr.yml"
    save_definition(path, definition)
    loaded = load_definition(path)
    assert loaded == definition

    outcome = render_definition(
        loaded,
        {"period": {"start": "2025-07-01", "end": "2025-10-01"}, "regions": ["APAC"]},
        warehouse=warehouse,
        warehouse_dialect=D,
        run=True,
        limit=50,
    )
    assert outcome.result is not None
    assert outcome.result.columns == ["month", "region", "realization_rate"]
    assert outcome.result.rows, "expected rows for Q3 2025 APAC"
    assert {row[1] for row in outcome.result.rows} == {"APAC"}
    assert {str(row[0])[:7] for row in outcome.result.rows} <= {
        "2025-07",
        "2025-08",
        "2025-09",
    }
