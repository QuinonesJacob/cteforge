"""The ClickHouse connector against a REAL ClickHouse server (opt-in; ADR 0025).

Skipped unless CTEFORGE_TEST_CLICKHOUSE_URL is set. No server is started by the tests:
point them at one you run (a local server or a test instance — never production).

1. Prepare the server once, as an administrator (creates database `agent_traces` with a
   synthetic trace dataset, settings profile `cteforge_ro_profile` and user `cteforge_ro`
   with SELECT only; see tests/fixtures/clickhouse/live_setup.py):

       export CTEFORGE_TEST_CLICKHOUSE_URL=http://localhost:8123   # or https://host:8443
       export CTEFORGE_TEST_CLICKHOUSE_ADMIN_USER=default
       export CTEFORGE_TEST_CLICKHOUSE_ADMIN_PASSWORD=...
       export CTEFORGE_TEST_CLICKHOUSE_PASSWORD=...                 # for cteforge_ro
       uv sync --extra clickhouse
       uv run python tests/fixtures/clickhouse/live_setup.py

2. Run the tests (they connect only as the read-only user):

       uv run pytest tests/integration/test_clickhouse_live.py -v

   Optional: CTEFORGE_TEST_CLICKHOUSE_USER (default cteforge_ro),
   CTEFORGE_TEST_CLICKHOUSE_VERIFY=0 for a self-signed TLS certificate.

Covers: column types, EXPLAIN, run with a row limit, readonly refusal of writes / DDL /
settings escapes, multi-statement refusal, timeout, scan guards, error scrubbing, the
documented week starts, and the trace definition end to end (same query back on the
server, then new values and a grain change).
"""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

import pytest

from cteforge.domain.codec import annotations_from_yaml
from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import WarehouseError
from cteforge.domain.issues import Severity
from cteforge.domain.models import Definition
from cteforge.parsing.analyze import analyze, referenced_tables
from cteforge.templating.render import render
from cteforge.templating.roundtrip import roundtrip_check
from cteforge.templating.template import apply_annotations

URL = os.environ.get("CTEFORGE_TEST_CLICKHOUSE_URL")
PASSWORD_VAR = "CTEFORGE_TEST_CLICKHOUSE_PASSWORD"
pytestmark = pytest.mark.skipif(
    not URL, reason="set CTEFORGE_TEST_CLICKHOUSE_URL to run the live ClickHouse tests"
)
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "clickhouse"
CH = Dialect.CLICKHOUSE
# Heavy enough to outlive a 1 s timeout (12k³ row combinations), no table function needed.
SLOW_SQL = (
    "SELECT count() FROM agent_traces.tool_calls AS a CROSS JOIN agent_traces.tool_calls AS b "
    "CROSS JOIN agent_traces.tool_calls AS c WHERE a.latency_ms + b.latency_ms + c.latency_ms = 7"
)


def profile(**overrides) -> WarehouseProfile:
    parsed = urlparse(URL or "")
    options: dict = {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or (8443 if parsed.scheme == "https" else 8123),
        "secure": parsed.scheme == "https",
        "database": "agent_traces",
        "user": os.environ.get("CTEFORGE_TEST_CLICKHOUSE_USER", "cteforge_ro"),
    }
    if os.environ.get("CTEFORGE_TEST_CLICKHOUSE_VERIFY") == "0":
        options["verify"] = False
    options |= overrides.pop("options", {})
    base = {
        "name": "ch_live",
        "dialect": CH,
        "options": options,
        "secrets_env": {"password": PASSWORD_VAR},
        "statement_timeout_s": 30,
    }
    return WarehouseProfile(**(base | overrides))


def connect(**overrides):
    pytest.importorskip("clickhouse_connect")
    from cteforge.warehouse.clickhouse import ClickHouseWarehouse

    return ClickHouseWarehouse(profile(**overrides))


@pytest.fixture(scope="module")
def wh():
    warehouse = connect()
    yield warehouse
    warehouse.close()


def trace_count(wh) -> int:
    return wh.run("SELECT count() FROM agent_traces.traces", 1).rows[0][0]


# ── Metadata, explain, run ──────────────────────────────────────────────────────────


def test_column_types(wh):
    types = wh.column_types("agent_traces.traces")
    assert types["started_at"] == "DateTime64(3, 'UTC')"
    assert types["model"] == "LowCardinality(String)"
    assert types["tags"] == "Array(LowCardinality(String))"
    assert wh.column_types("traces") == types  # unqualified: the profile's database
    assert wh.column_types("agent_traces.no_such_table") == {}


def test_explain(wh):
    wh.explain("SELECT model, count() FROM agent_traces.traces GROUP BY model")
    with pytest.raises(WarehouseError) as info:
        wh.explain("SELECT * FROM agent_traces.no_such_table")
    assert "version" not in str(info.value) and "Stack trace" not in str(info.value)


def test_run_with_limit(wh):
    result = wh.run("SELECT trace_id FROM agent_traces.traces ORDER BY trace_id", 5)
    assert result.columns == ["trace_id"]
    assert result.rows == [(0,), (1,), (2,), (3,), (4,)]
    assert result.truncated is True
    small = wh.run("SELECT 1 AS one", 5)
    assert small.rows == [(1,)] and small.truncated is False


def test_interval_with_a_quoted_count_is_valid_clickhouse(wh):
    """sqlglot writes INTERVAL 7 DAY as INTERVAL '7' DAY; ClickHouse documents both."""
    rows = wh.run("SELECT (now() - INTERVAL '7' DAY) = (now() - INTERVAL 7 DAY)", 1).rows
    assert rows == [(1,)] or rows == [(True,)]


def test_week_starts_match_design_section_13(wh):
    thursday = "toDate('2026-01-08')"
    sql = (
        f"SELECT toMonday({thursday}), dateTrunc('week', {thursday}), "
        f"toStartOfWeek({thursday}), toStartOfWeek({thursday}, 1), "
        f"toStartOfInterval({thursday}, INTERVAL 1 WEEK)"
    )
    (row,) = wh.run(sql, 1).rows
    monday, sunday = date(2026, 1, 5), date(2026, 1, 4)
    as_dates = [v.date() if isinstance(v, datetime) else v for v in row]
    assert as_dates == [monday, monday, sunday, monday, monday]


# ── Read-only, single statement ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO agent_traces.traces (trace_id) VALUES (999999)",
        "DROP TABLE agent_traces.traces",
        "CREATE TABLE agent_traces.x (a UInt8) ENGINE = Memory",
        "ALTER TABLE agent_traces.traces DELETE WHERE 1",
        "TRUNCATE TABLE agent_traces.traces",
        "OPTIMIZE TABLE agent_traces.traces FINAL",
        "CREATE TEMPORARY TABLE tmp (a UInt8)",
        "SELECT 1 SETTINGS readonly = 0",
        "SELECT * FROM url('http://169.254.169.254/latest/meta-data/', 'RawBLOB')",
    ],
)
def test_writes_ddl_and_escapes_are_refused(wh, sql):
    before = trace_count(wh)
    with pytest.raises(WarehouseError):
        wh.run(sql, 10)
    assert trace_count(wh) == before


def test_multi_statement_is_refused(wh):
    with pytest.raises(WarehouseError, match="(?i)multi-statements"):
        wh.run("SELECT 1; DROP TABLE agent_traces.traces", 10)
    assert trace_count(wh) == 3000


def test_profile_constraint_caps_a_query_raising_its_own_timeout(wh):
    """The admin's settings profile (MAX 300) is what stops a SETTINGS clause escape."""
    with pytest.raises(WarehouseError, match="max_execution_time"):
        wh.run("SELECT 1 SETTINGS max_execution_time = 100000", 1)


# ── Timeout, scan guards, scrubbing ─────────────────────────────────────────────────


def test_timeout():
    warehouse = connect(statement_timeout_s=1)
    try:
        with pytest.raises(WarehouseError, match=r"^query timed out after 1s$"):
            warehouse.run(SLOW_SQL, 1)
    finally:
        warehouse.close()


@pytest.mark.parametrize(
    ("guard", "pattern"), [("max_rows_to_read", "rows"), ("max_bytes_to_read", "bytes")]
)
def test_scan_guards(guard, pattern):
    warehouse = connect(options={guard: 100})
    try:
        with pytest.raises(WarehouseError, match=f"(?i){pattern}"):
            warehouse.run("SELECT sum(latency_ms) FROM agent_traces.tool_calls", 1)
        assert warehouse.run("SELECT 1", 1).rows == [(1,)]  # no table read: allowed
    finally:
        warehouse.close()


def test_wrong_password_error_is_scrubbed(monkeypatch):
    monkeypatch.setenv(PASSWORD_VAR, "definitely-not-the-password-1234")
    user = os.environ.get("CTEFORGE_TEST_CLICKHOUSE_USER", "cteforge_ro")
    host = urlparse(URL or "").hostname or ""
    with pytest.raises(WarehouseError) as info:
        connect()
    message = str(info.value)
    for leaked in ("definitely-not-the-password-1234", user, host, "(version"):
        assert leaked not in message, message


# ── The trace definition end to end ─────────────────────────────────────────────────


def trace_definition(wh) -> tuple[str, Definition]:
    sql = (FIXTURES / "trace_tool_errors.sql").read_text()
    schema = {t: wh.column_types(t) for t in referenced_tables(sql, CH)}
    meta = annotations_from_yaml((FIXTURES / "trace_tool_errors.annotations.yml").read_text())
    annotated = apply_annotations(sql, CH, analyze(sql, CH, schema), meta.variables)
    assert [i for i in annotated.issues if i.severity is Severity.ERROR] == []
    definition = Definition(
        id=meta.id,
        title=meta.title,
        source_dialect=CH,
        template=annotated.template,
        variables=annotated.variables,
    )
    return sql, definition


def test_trace_definition_same_query_back_on_the_server(wh):
    sql, definition = trace_definition(wh)
    assert roundtrip_check(sql, definition).identical
    original = wh.run(sql, 10_000)
    rendered = wh.run(render(definition, {}).sql, 10_000)
    assert original.rows and rendered.rows == original.rows
    assert rendered.columns == ["period", "tool", "calls", "errors", "error_rate"]


def test_trace_definition_new_values_and_weekly_grain(wh):
    _, definition = trace_definition(wh)
    values = {
        "period": {"start": "2026-01-05", "end": "2026-01-19"},
        "models": ["claude-haiku-4"],
        "required_tag": "production",
        "time_grain": "week",
    }
    result = wh.run(render(definition, values).sql, 10_000)
    expected = wh.run(
        "SELECT toMonday(tc.called_at) AS period, tc.tool_name AS tool, count() AS calls, "
        "countIf(tc.status = 'error') AS errors, "
        "round(countIf(tc.status = 'error') / count(), 4) AS error_rate "
        "FROM agent_traces.tool_calls AS tc "
        "INNER JOIN agent_traces.turns AS u ON u.turn_id = tc.turn_id "
        "INNER JOIN agent_traces.traces AS t ON t.trace_id = u.trace_id "
        "WHERE t.started_at >= toDateTime64('2026-01-05', 3, 'UTC') "
        "AND t.started_at < toDateTime64('2026-01-19', 3, 'UTC') "
        "AND t.model IN ('claude-haiku-4') AND has(t.tags, 'production') "
        "GROUP BY period, tool ORDER BY period, tool",
        10_000,
    )
    assert result.rows and result.rows == expected.rows
    assert all(row[0].weekday() == 0 for row in result.rows)  # Mondays
