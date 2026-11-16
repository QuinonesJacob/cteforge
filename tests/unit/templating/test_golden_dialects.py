"""Golden per-dialect SQL (ADR 0012). Regenerate deliberately: UPDATE_GOLDEN=1 uv run pytest"""

import os
from pathlib import Path

import pytest

from cteforge.domain.dialects import Dialect
from cteforge.templating.render import render

GOLDEN = Path(__file__).resolve().parents[2] / "golden"
VALUES = {"period": {"start": "2026-07-01", "end": "2026-10-01"}, "regions": ["EMEA"]}


@pytest.mark.parametrize(
    "dialect", [Dialect.DUCKDB, Dialect.SNOWFLAKE, Dialect.BIGQUERY, Dialect.POSTGRES]
)
def test_golden(sample_definition, dialect):
    result = render(sample_definition, VALUES, dialect)
    path = GOLDEN / f"realization_rate.{dialect}.sql"
    if os.environ.get("UPDATE_GOLDEN") or not path.exists():
        path.write_text(result.sql + "\n")
    assert result.sql + "\n" == path.read_text()
    assert result.transpiled is (dialect is not Dialect.DUCKDB)
