from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from cteforge.domain.codec import AnnotationFile, annotations_from_yaml
from cteforge.domain.dialects import Dialect
from cteforge.domain.models import Definition
from cteforge.parsing.analyze import analyze
from cteforge.templating.template import apply_annotations

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


@pytest.fixture(scope="session")
def sample_sql() -> str:
    return (EXAMPLES / "queries" / "realization_rate.sql").read_text()


@pytest.fixture(scope="session")
def sample_annotations() -> AnnotationFile:
    return annotations_from_yaml(
        (EXAMPLES / "definitions" / "realization_rate.annotations.yml").read_text()
    )


@pytest.fixture(scope="session")
def warehouse_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    spec = importlib.util.spec_from_file_location("seed", EXAMPLES / "seed_warehouse.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = tmp_path_factory.mktemp("wh") / "warehouse.duckdb"
    module.seed(path)
    return path


@pytest.fixture(scope="session")
def warehouse(warehouse_path: Path):
    from cteforge.warehouse.duckdb import DuckDBWarehouse

    wh = DuckDBWarehouse(warehouse_path)
    yield wh
    wh.close()


# Static copy of the seeded warehouse schema so unit tests need no database.
# tests/integration/test_import_and_render.py asserts it matches the real warehouse.
SAMPLE_SCHEMA = {
    "accounts": {"account_id": "INTEGER", "name": "VARCHAR", "region": "VARCHAR"},
    "deals": {
        "deal_id": "INTEGER",
        "account_id": "INTEGER",
        "closed_at": "TIMESTAMP",
        "status": "VARCHAR",
        "amount": "DECIMAL(12,2)",
    },
    "invoices": {
        "invoice_id": "INTEGER",
        "deal_id": "INTEGER",
        "invoiced_at": "TIMESTAMP",
        "status": "VARCHAR",
        "paid_amount": "DECIMAL(12,2)",
    },
}


@pytest.fixture(scope="session")
def sample_schema() -> dict[str, dict[str, str]]:
    return SAMPLE_SCHEMA


def build_definition(sql: str, annotations: AnnotationFile, schema: dict) -> Definition:
    """Definition from an example query + annotations, typed with a static schema."""
    analysis = analyze(sql, Dialect.DUCKDB, schema)
    annotated = apply_annotations(sql, Dialect.DUCKDB, analysis, annotations.variables)
    return Definition(
        id=annotations.id,
        title=annotations.title,
        source_dialect=Dialect.DUCKDB,
        template=annotated.template,
        variables=annotated.variables,
        tables=analysis.tables,
        outputs=analysis.outputs,
    )


@pytest.fixture(scope="session")
def sample_definition(sample_sql, sample_annotations, sample_schema) -> Definition:
    return build_definition(sample_sql, sample_annotations, sample_schema)


@pytest.fixture(scope="session")
def collections_sql() -> str:
    """Second example: multi-place `period`, strptime-wrapped dates, number with a unit."""
    return (EXAMPLES / "queries" / "booked_vs_collected.sql").read_text()


@pytest.fixture(scope="session")
def collections_annotations() -> AnnotationFile:
    return annotations_from_yaml(
        (EXAMPLES / "definitions" / "booked_vs_collected.annotations.yml").read_text()
    )


@pytest.fixture(scope="session")
def collections_definition(collections_sql, collections_annotations, sample_schema) -> Definition:
    return build_definition(collections_sql, collections_annotations, sample_schema)
