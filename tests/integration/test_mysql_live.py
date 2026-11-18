"""The MySQL connector against a REAL MySQL 8 server (opt-in; ADR 0028).

Skipped unless CTEFORGE_TEST_MYSQL_URL is set. No server is started by the tests: point them
at one you run (a local server, a CI service container or a test instance — never
production).

1. Prepare the server once, as an administrator (creates database `shop` with a synthetic
   dataset, user `cteforge_ro` with SELECT only, and the write probe `cteforge_probe`; see
   tests/fixtures/mysql/live_setup.py):

       export CTEFORGE_TEST_MYSQL_URL=mysql://127.0.0.1:3306
       export CTEFORGE_TEST_MYSQL_ADMIN_USER=root
       export CTEFORGE_TEST_MYSQL_ADMIN_PASSWORD=...
       export CTEFORGE_TEST_MYSQL_PASSWORD=...                  # for cteforge_ro
       uv sync --extra mysql
       uv run python tests/fixtures/mysql/live_setup.py

2. Run the tests (they connect only as cteforge_ro, and cteforge_probe for one test):

       uv run pytest tests/integration/test_mysql_live.py -v

   TLS: verified by default. For a server with an auto-generated certificate (the mysql:8
   image) set CTEFORGE_TEST_MYSQL_VERIFY=0 (encrypted, not verified), or
   CTEFORGE_TEST_MYSQL_SSL_CA=<ca.pem> to verify against that CA without the host name;
   CTEFORGE_TEST_MYSQL_SSL_DISABLED=1 for a server without TLS. Optional:
   CTEFORGE_TEST_MYSQL_USER (default cteforge_ro).

Covers: column types, EXPLAIN, run with a row limit, refusal of writes / DDL / INTO OUTFILE
/ multi-statements, the READ ONLY transaction, timeout, error scrubbing, hostile text values
read back exactly by the server, the documented week starts, and the shop definition end to
end (same query back on the server, then every grain and new values).
"""

from __future__ import annotations

import json
import os
from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

import pytest

from cteforge.domain.codec import annotations_from_yaml
from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import WarehouseError
from cteforge.domain.issues import Severity
from cteforge.domain.models import Annotation, Definition
from cteforge.domain.variables import VariableType
from cteforge.parsing.analyze import analyze, referenced_tables
from cteforge.templating.render import render
from cteforge.templating.roundtrip import roundtrip_check
from cteforge.templating.template import apply_annotations

URL = os.environ.get("CTEFORGE_TEST_MYSQL_URL")
PASSWORD_VAR = "CTEFORGE_TEST_MYSQL_PASSWORD"
pytestmark = pytest.mark.skipif(
    not URL, reason="set CTEFORGE_TEST_MYSQL_URL to run the live MySQL tests"
)
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "mysql"
MY = Dialect.MYSQL
# 5,000³ row combinations: outlives a 1 s timeout.
SLOW_SQL = (
    "SELECT COUNT(*) FROM shop.orders AS a CROSS JOIN shop.orders AS b "
    "CROSS JOIN shop.orders AS c WHERE a.amount + b.amount + c.amount = 7"
)


def profile(**overrides) -> WarehouseProfile:
    parsed = urlparse(URL or "")
    options: dict = {
        "host": parsed.hostname or "127.0.0.1",
        "port": parsed.port or 3306,
        "database": "shop",
        "user": os.environ.get("CTEFORGE_TEST_MYSQL_USER", "cteforge_ro"),
    }
    if os.environ.get("CTEFORGE_TEST_MYSQL_SSL_DISABLED") == "1":
        options["ssl_disabled"] = True
    elif os.environ.get("CTEFORGE_TEST_MYSQL_SSL_CA"):
        options["ssl_ca"] = os.environ["CTEFORGE_TEST_MYSQL_SSL_CA"]
        options["ssl_verify_identity"] = False
    elif os.environ.get("CTEFORGE_TEST_MYSQL_VERIFY") == "0":
        options["ssl_verify_cert"] = False
    options |= overrides.pop("options", {})
    base = {
        "name": "mysql_live",
        "dialect": MY,
        "options": options,
        "secrets_env": {"password": PASSWORD_VAR},
        "statement_timeout_s": 30,
    }
    return WarehouseProfile(**(base | overrides))


def connect(**overrides):
    pytest.importorskip("pymysql")
    from cteforge.warehouse.mysql import MySQLWarehouse

    return MySQLWarehouse(profile(**overrides))


@pytest.fixture(scope="module")
def wh():
    warehouse = connect()
    yield warehouse
    warehouse.close()


def order_count(wh) -> int:
    return wh.run("SELECT COUNT(*) FROM shop.orders", 1).rows[0][0]


# ── Metadata, explain, run ──────────────────────────────────────────────────────────


def test_column_types(wh):
    types = wh.column_types("shop.orders")
    assert types["order_id"] == "bigint unsigned"
    assert types["is_test"] == "tinyint(1)"
    assert types["status"] == "enum('new','paid','shipped','refunded')"
    assert types["amount"] == "decimal(10,2)"
    assert types["created_at"] == "datetime"
    assert wh.column_types("orders") == types  # unqualified: the profile's database
    assert wh.column_types("shop.no_such_table") == {}


def test_explain(wh):
    wh.explain("SELECT channel, COUNT(*) FROM shop.orders GROUP BY channel")
    with pytest.raises(WarehouseError, match="doesn't exist"):
        wh.explain("SELECT * FROM shop.no_such_table")


def test_run_with_limit(wh):
    result = wh.run("SELECT order_id FROM shop.orders ORDER BY order_id", 5)
    assert result.columns == ["order_id"]
    assert result.rows == [(1,), (2,), (3,), (4,), (5,)]
    assert result.truncated is True
    small = wh.run("SELECT 1 AS one", 5)
    assert small.rows == [(1,)] and small.truncated is False


def test_wide_results_are_cut_and_the_connection_stays_usable(wh):
    result = wh.run("SELECT a.order_id FROM shop.orders AS a CROSS JOIN shop.customers AS b", 3)
    assert len(result.rows) == 3 and result.truncated is True
    assert order_count(wh) == 5000


# ── Read-only, single statement ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO shop.orders (order_id) VALUES (999999)",
        "UPDATE shop.orders SET amount = 0",
        "DELETE FROM shop.orders",
        "DROP TABLE shop.orders",
        "TRUNCATE TABLE shop.orders",
        "CREATE TABLE shop.x (a INT)",
        "CREATE TEMPORARY TABLE tmp (a INT)",
        "SELECT * FROM shop.orders INTO OUTFILE '/var/lib/mysql-files/cteforge.txt'",
        "SELECT * FROM shop.orders INTO DUMPFILE '/tmp/cteforge.bin'",
        "GRANT SELECT ON shop.* TO 'cteforge_ro'@'%'",
        "SET GLOBAL max_execution_time = 0",
    ],
)
def test_writes_ddl_and_server_files_are_refused(wh, sql):
    """The grants (SELECT only, no FILE) are the guarantee; the transaction is READ ONLY."""
    with pytest.raises(WarehouseError):
        wh.run(sql, 10)
    assert order_count(wh) == 5000


def test_load_file_reads_nothing_without_the_file_privilege(wh):
    assert wh.run("SELECT LOAD_FILE('/etc/passwd') AS f", 1).rows == [(None,)]


def test_multi_statement_is_refused(wh):
    with pytest.raises(WarehouseError, match="(?i)syntax"):
        wh.run("SELECT 1; DROP TABLE shop.orders", 10)
    assert order_count(wh) == 5000


def test_read_only_transaction_refuses_a_write_the_grants_allow():
    """cteforge_probe may INSERT into shop.probe; the connector's READ ONLY transaction
    still refuses it (defence in depth)."""
    probe = connect(options={"user": "cteforge_probe"})
    try:
        with pytest.raises(WarehouseError, match="(?i)read only"):
            probe.run("INSERT INTO shop.probe (id) VALUES (1)", 1)
        assert probe.run("SELECT COUNT(*) FROM shop.probe", 1).rows == [(0,)]
    finally:
        probe.close()


# ── Timeout, scrubbing ──────────────────────────────────────────────────────────────


def test_timeout_and_the_connection_works_afterwards():
    warehouse = connect(statement_timeout_s=1)
    try:
        with pytest.raises(WarehouseError, match=r"^query timed out after 1s$"):
            warehouse.run(SLOW_SQL, 1)
        assert warehouse.run("SELECT 1", 1).rows == [(1,)]
    finally:
        warehouse.close()


def test_wrong_password_error_is_scrubbed(monkeypatch):
    monkeypatch.setenv(PASSWORD_VAR, "definitely-not-the-password-1234")
    user = os.environ.get("CTEFORGE_TEST_MYSQL_USER", "cteforge_ro")
    host = urlparse(URL or "").hostname or ""
    with pytest.raises(WarehouseError, match="Access denied") as info:
        connect()
    message = str(info.value)
    for leaked in ("definitely-not-the-password-1234", f"'{user}'", host, "@'"):
        assert leaked not in message, message


# ── Literals and week starts as the server reads them ──────────────────────────────


def test_hostile_text_values_are_read_back_exactly(wh):
    values = json.loads((FIXTURES / "hostile_strings.json").read_text(encoding="ascii"))
    sql = "SELECT s.id FROM shop.strings AS s WHERE s.s = 'x'"
    schema = {"shop.strings": wh.column_types("shop.strings")}
    annotations = [Annotation(name="s", type=VariableType.TEXT, literals=["main.L1"])]
    annotated = apply_annotations(sql, MY, analyze(sql, MY, schema), annotations)
    definition = Definition(
        id="s",
        title="s",
        source_dialect=MY,
        template=annotated.template,
        variables=annotated.variables,
    )
    for n, value in enumerate(values, start=1):
        result = wh.run(render(definition, {"s": value}).sql, 10)
        assert result.rows == [(n,)], repr(value)


def test_week_and_quarter_starts_match_design_section_13(wh):
    thursday, may = "DATE '2026-01-08'", "DATE '2026-05-20'"
    sql = (
        f"SELECT DATE_FORMAT({thursday} - INTERVAL WEEKDAY({thursday}) DAY, '%Y-%m-%d'), "
        f"DATE_FORMAT(MAKEDATE(YEAR({may}), 1) + INTERVAL QUARTER({may}) - 1 QUARTER, "
        "'%Y-%m-%d'), "
        # What sqlglot writes for DATE_TRUNC transpiled from other dialects (ADR 0028).
        "DATE_ADD('0000-01-01 00:00:00', INTERVAL (TIMESTAMPDIFF(WEEK, '0000-01-01 00:00:00', "
        f"{thursday})) WEEK), "
        "DATE_ADD('0000-01-01 00:00:00', INTERVAL (TIMESTAMPDIFF(MONTH, '0000-01-01 00:00:00', "
        f"{may})) MONTH)"
    )
    (row,) = wh.run(sql, 1).rows
    as_text = [str(v)[:10] for v in row]
    assert as_text == ["2026-01-05", "2026-04-01", "2026-01-04", "2026-05-01"]


# ── The shop definition end to end ──────────────────────────────────────────────────


def shop_definition(wh) -> tuple[str, Definition]:
    sql = (FIXTURES / "paid_revenue.sql").read_text()
    schema = {t: wh.column_types(t) for t in referenced_tables(sql, MY)}
    meta = annotations_from_yaml((FIXTURES / "paid_revenue.annotations.yml").read_text())
    annotated = apply_annotations(sql, MY, analyze(sql, MY, schema), meta.variables)
    assert [i for i in annotated.issues if i.severity is Severity.ERROR] == []
    definition = Definition(
        id=meta.id,
        title=meta.title,
        source_dialect=MY,
        template=annotated.template,
        variables=annotated.variables,
    )
    return sql, definition


def test_shop_definition_same_query_back_on_the_server(wh):
    sql, definition = shop_definition(wh)
    assert roundtrip_check(sql, definition).identical
    original = wh.run(sql, 10_000)
    rendered = wh.run(render(definition, {}).sql, 10_000)
    assert original.rows and rendered.rows == original.rows
    assert rendered.columns == ["period", "channel", "orders", "revenue"]
    assert {row[0] for row in rendered.rows} == {"2026-01-01", "2026-02-01", "2026-03-01"}


@pytest.mark.parametrize("unit", ["day", "week", "month", "quarter", "year"])
def test_every_grain_runs(wh, unit):
    _, definition = shop_definition(wh)
    result = wh.run(render(definition, {"time_grain": unit}).sql, 10_000)
    periods = {date.fromisoformat(row[0]) for row in result.rows}
    assert periods
    if unit == "week":
        assert all(p.weekday() == 0 for p in periods)  # Mondays
    elif unit != "day":
        assert all(p.day == 1 for p in periods)
    if unit in ("quarter", "year"):
        assert periods == {date(2026, 1, 1)}


def test_shop_definition_new_values_and_weekly_grain(wh):
    _, definition = shop_definition(wh)
    values = {
        "period": {"start": "2026-01-05", "end": "2026-02-02"},
        "statuses": ["paid"],
        "country": "FR",
        "min_amount": 25,
        "time_grain": "week",
    }
    result = wh.run(render(definition, values).sql, 10_000)
    expected = wh.run(
        "SELECT DATE_FORMAT(DATE_SUB(o.created_at, INTERVAL WEEKDAY(o.created_at) DAY), "
        "'%Y-%m-%d') AS period, o.channel, COUNT(*) AS orders, SUM(o.amount) AS revenue "
        "FROM shop.orders AS o JOIN shop.customers AS c ON c.customer_id = o.customer_id "
        "WHERE o.created_at >= '2026-01-05' AND o.created_at < '2026-02-02' "
        "AND o.status = 'paid' AND c.country = 'FR' AND o.is_test = 0 AND o.amount >= 25 "
        "GROUP BY 1, 2 ORDER BY 1, 2",
        10_000,
    )
    assert result.rows and result.rows == expected.rows
    assert all(isinstance(row[3], Decimal) for row in result.rows)
