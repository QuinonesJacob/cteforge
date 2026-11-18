"""The Postgres connector and the whole import → run flow against a REAL Postgres server
(opt-in; ADR 0020). Runs in CI against a postgres service container.

Skipped unless CTEFORGE_TEST_POSTGRES_URL is set. No server is started by the tests.

1. Prepare the server once, as an administrator (database `cteforge_test` with a copy of the
   synthetic example warehouse and a role `cteforge_ro` with SELECT only; see
   tests/fixtures/postgres/live_setup.py):

       export CTEFORGE_TEST_POSTGRES_URL=postgresql://localhost:5432
       export CTEFORGE_TEST_POSTGRES_ADMIN_USER=postgres
       export CTEFORGE_TEST_POSTGRES_ADMIN_PASSWORD=...
       export CTEFORGE_TEST_POSTGRES_PASSWORD=...                  # for cteforge_ro
       uv sync --extra postgres
       uv run python examples/seed_warehouse.py
       uv run python tests/fixtures/postgres/live_setup.py

2. Run the tests (they connect only as the read-only role):

       uv run pytest tests/integration/test_postgres_live.py -v

Covers: column types, EXPLAIN, run with a row limit, refusal of writes / DDL /
multi-statement strings, the statement timeout, error scrubbing, and the example
`realization_rate` definitions end to end: same query back on the server, the services'
proposal checks (EXPLAIN + result comparison on Postgres), compile_and_run with new values
and a grain change, and equality with the same query on the DuckDB example warehouse.
"""

from __future__ import annotations

import datetime as dt
import os
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

import duckdb
import pytest
import yaml

from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import WarehouseError
from cteforge.domain.models import DefinitionState
from cteforge.domain.users import User, UserRole

URL = os.environ.get("CTEFORGE_TEST_POSTGRES_URL")
PASSWORD_VAR = "CTEFORGE_TEST_POSTGRES_PASSWORD"
pytestmark = pytest.mark.skipif(
    not URL, reason="set CTEFORGE_TEST_POSTGRES_URL to run the live Postgres tests"
)
EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
SQL = (EXAMPLES / "queries" / "realization_rate.sql").read_text()
ANN = yaml.safe_load(
    (EXAMPLES / "definitions" / "realization_rate_grain.annotations.yml").read_text()
)
PG = Dialect.POSTGRES
META_KEYS = ("id", "title", "meaning", "keywords", "use_when", "dont_use_when", "grain")


def profile(**overrides) -> WarehouseProfile:
    parsed = urlparse(URL or "")
    options = {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "dbname": "cteforge_test",
        "user": os.environ.get("CTEFORGE_TEST_POSTGRES_USER", "cteforge_ro"),
    } | overrides.pop("options", {})
    base = {
        "name": "pg_live",
        "dialect": PG,
        "options": options,
        "secrets_env": {"password": PASSWORD_VAR},
        "statement_timeout_s": 30,
    }
    return WarehouseProfile(**(base | overrides))


def connect(**overrides):
    pytest.importorskip("psycopg")
    from cteforge.warehouse.postgres import PostgresWarehouse

    return PostgresWarehouse(profile(**overrides))


@pytest.fixture(scope="module")
def wh():
    warehouse = connect()
    yield warehouse
    warehouse.close()


def deal_count(wh) -> int:
    return wh.run("SELECT count(*) FROM deals", 1).rows[0][0]


# ── Metadata, explain, run ──────────────────────────────────────────────────────────


def test_column_types(wh):
    types = wh.column_types("deals")
    assert types["closed_at"] == "timestamp without time zone"
    assert types["amount"] == "numeric"
    assert types["status"] == "character varying"
    assert wh.column_types("public.deals") == types
    assert wh.column_types("cteforge_test.public.deals") == types
    assert wh.column_types("no_such_table") == {}


def test_explain(wh):
    wh.explain("SELECT status, count(*) FROM deals GROUP BY status")
    with pytest.raises(WarehouseError, match="no_such_table"):
        wh.explain("SELECT * FROM no_such_table")


def test_run_with_limit(wh):
    result = wh.run("SELECT deal_id FROM deals ORDER BY deal_id", 5)
    assert result.columns == ["deal_id"]
    assert result.rows == [(1,), (2,), (3,), (4,), (5,)]
    assert result.truncated is True
    small = wh.run("SELECT 1 AS one", 5)
    assert small.rows == [(1,)] and small.truncated is False


# ── Read-only, single statement ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO deals (deal_id) VALUES (999999)",
        "UPDATE deals SET amount = 0",
        "DELETE FROM deals",
        "CREATE TABLE x (a INTEGER)",
        "DROP TABLE deals",
        "TRUNCATE deals",
        "WITH gone AS (DELETE FROM deals RETURNING 1) SELECT count(*) FROM gone",
        "SELECT * FROM deals FOR UPDATE",
    ],
)
def test_writes_and_ddl_are_refused(wh, sql):
    before = deal_count(wh)
    with pytest.raises(WarehouseError):
        wh.run(sql, 10)
    assert deal_count(wh) == before


def test_a_statement_cannot_make_its_transaction_read_write(wh):
    with pytest.raises(WarehouseError):
        wh.run("SET TRANSACTION READ WRITE", 1)
    # Changing the session default does not reach the next call's BEGIN READ ONLY.
    wh.run("SELECT set_config('default_transaction_read_only', 'off', false)", 1)
    with pytest.raises(WarehouseError):
        wh.run("INSERT INTO deals (deal_id) VALUES (999999)", 1)


def test_multi_statement_is_refused(wh):
    with pytest.raises(WarehouseError, match="(?i)multiple commands"):
        wh.run("SELECT 1; DROP TABLE deals", 10)
    assert deal_count(wh) == 2000


# ── Timeout, scrubbing ──────────────────────────────────────────────────────────────


def test_timeout():
    warehouse = connect(statement_timeout_s=1)
    try:
        with pytest.raises(WarehouseError, match=r"^query timed out after 1s$"):
            warehouse.run("SELECT pg_sleep(5)", 1)
        assert warehouse.run("SELECT 1", 1).rows == [(1,)]  # the session is usable after
    finally:
        warehouse.close()


def test_wrong_password_error_is_scrubbed(monkeypatch):
    monkeypatch.setenv(PASSWORD_VAR, "definitely-not-the-password-1234")
    with pytest.raises(WarehouseError) as info:
        connect()
    message = str(info.value)
    assert "definitely-not-the-password-1234" not in message, message
    assert (urlparse(URL or "").hostname or "localhost") not in message, message


# ── The example definition end to end ───────────────────────────────────────────────


def _normalized(rows):
    """Cross-engine comparison: DuckDB returns ROUND(decimal / decimal) as a float where
    Postgres returns NUMERIC; timestamps compare as text."""

    def one(v):
        if isinstance(v, Decimal | float):
            return f"{float(v):.4f}"
        return str(v) if isinstance(v, dt.datetime | dt.date) else v

    return [tuple(one(v) for v in r) for r in rows]


def test_definition_same_query_back_and_equal_to_duckdb(wh):
    from cteforge.domain.codec import annotations_from_yaml
    from cteforge.domain.models import Definition
    from cteforge.parsing.analyze import analyze, referenced_tables
    from cteforge.templating.render import render
    from cteforge.templating.roundtrip import roundtrip_check
    from cteforge.templating.template import apply_annotations

    schema = {t: wh.column_types(t) for t in referenced_tables(SQL, PG)}
    meta = annotations_from_yaml(
        (EXAMPLES / "definitions" / "realization_rate.annotations.yml").read_text()
    )
    annotated = apply_annotations(SQL, PG, analyze(SQL, PG, schema), meta.variables)
    definition = Definition(
        id=meta.id,
        title=meta.title,
        source_dialect=PG,
        template=annotated.template,
        variables=annotated.variables,
    )
    assert roundtrip_check(SQL, definition).identical
    original = wh.run(SQL, 10_000)
    assert original.rows and wh.run(render(definition, {}).sql, 10_000).rows == original.rows
    local = duckdb.connect(str(EXAMPLES / "warehouse.duckdb"), read_only=True)
    try:
        expected = local.execute(SQL).fetchall()
    finally:
        local.close()
    assert _normalized(original.rows) == _normalized(expected)


@pytest.fixture
def ctx(tmp_path):
    from cteforge.services.context import AppConfig, open_context

    with open_context(
        AppConfig(db_url=f"sqlite:///{tmp_path / 'store.db'}"), profiles=[profile()]
    ) as c:
        yield c


def test_services_import_checks_and_compile_and_run_on_postgres(ctx):
    pytest.importorskip("psycopg")
    from cteforge.services.propose import propose_definition
    from cteforge.services.run import compile_and_run

    alice = User(name="alice", role=UserRole.CONTRIBUTOR)
    out = propose_definition(
        ctx,
        alice,
        sql=SQL,
        dialect="postgres",
        metadata={k: ANN[k] for k in META_KEYS},
        annotations=[dict(a) for a in ANN["variables"]],
    )
    errors = [i for i in out.issues if i.severity.value == "error"]
    assert errors == [], errors
    assert out.auto_applied and out.definition is not None
    assert out.definition.state is DefinitionState.LLM_ANNOTATED  # checks ran on Postgres

    result = compile_and_run(
        ctx,
        alice,
        uses={"rr": {"id": "realization_rate_by_grain"}},
        values={
            "rr": {
                "period": {"start": "2026-01-01", "end": "2026-07-01"},
                "regions": ["APAC"],
                "time_grain": "quarter",
            }
        },
    )
    assert result.ran and result.rows, result.issues
    assert {row[1] for row in result.rows} == {"APAC"}
    quarters = {row[0] for row in result.rows}
    assert quarters == {"2026-01-01T00:00:00", "2026-04-01T00:00:00"}  # JSON-safe rows
    assert "/* cteforge: realization_rate_by_grain v1" in result.sql
