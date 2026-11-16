"""compared_type for columns computed in a CTE (aggregates, arithmetic)."""

from cteforge.domain.dialects import Dialect
from cteforge.parsing.analyze import analyze

SCHEMA = {"deals": {"amount": "DECIMAL(12,2)", "region": "VARCHAR", "n": "INTEGER"}}
SQL = """
WITH b AS (SELECT region, SUM(amount) AS booked, MAX(n) AS top, COUNT(*) AS c
           FROM deals GROUP BY region)
SELECT * FROM b WHERE booked >= 1000 AND top > 5 AND c > 2
"""


def test_aggregated_cte_columns_are_typed():
    analysis = analyze(SQL, Dialect.DUCKDB, SCHEMA)
    types = {lit.column_sql: lit.compared_type for lit in analysis.literals}
    assert types == {"booked": "DECIMAL(12, 2)", "top": "INT", "c": "BIGINT"}
