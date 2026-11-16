"""Golden per-dialect SQL (ADR 0012, 0013).

A missing golden file FAILS the test. Files are (re)written only deliberately:
    UPDATE_GOLDEN=1 uv run pytest tests/unit/templating/test_golden_dialects.py
and then reviewed as a diff.

Known transpile defects kept visible in the goldens (sqlglot 30.31.0; not fixed by us —
transpiled renders carry a `transpiled` issue and lower trust, ADR 0013):
- realization_rate.postgres.sql: `ROUND(CAST(... AS DOUBLE PRECISION) / ..., 4)` —
  Postgres has no ROUND(double precision, integer); the query fails there.
- realization_rate.bigquery.sql: `TIMESTAMP_TRUNC(cd.closed_at, MONTH)` on a column that is
  DATETIME in BigQuery (DuckDB TIMESTAMP is zone-naive); BigQuery needs DATETIME_TRUNC.
- booked_vs_collected.bigquery.sql: `PARSE_TIMESTAMP(...)` yields a zone-aware TIMESTAMP
  compared with a DATETIME column (DuckDB strptime is naive).
- booked_vs_collected.postgres.sql: `TO_TIMESTAMP(...)` yields timestamptz compared with a
  naive timestamp column (session time zone applies).
"""

import os
from pathlib import Path

import pytest

from cteforge.domain.dialects import Dialect
from cteforge.domain.issues import IssueCode
from cteforge.templating.render import render

GOLDEN = Path(__file__).resolve().parents[2] / "golden"
CASES = {
    "realization_rate": (
        "sample_definition",
        {"period": {"start": "2026-07-01", "end": "2026-10-01"}, "regions": ["EMEA"]},
    ),
    "booked_vs_collected": (
        "collections_definition",
        {"period": {"start": "2026-01-15", "end": "2026-03-01"}, "min_booked": 1000.5},
    ),
}


@pytest.mark.parametrize("dialect", list(Dialect))
@pytest.mark.parametrize("case", sorted(CASES))
def test_golden(request, case, dialect):
    fixture, values = CASES[case]
    result = render(request.getfixturevalue(fixture), values, dialect)
    path = GOLDEN / f"{case}.{dialect}.sql"
    if os.environ.get("UPDATE_GOLDEN") == "1":
        path.write_text(result.sql + "\n")
    assert path.exists(), f"Missing golden file {path.name}; create it with UPDATE_GOLDEN=1"
    assert result.sql + "\n" == path.read_text()
    assert result.transpiled is (dialect is not Dialect.DUCKDB)
    assert (IssueCode.TRANSPILED in [i.code for i in result.issues]) is result.transpiled
