"""Read-only validation for MySQL SQL (ADR 0028): compose.checks.ensure_query is the guard
that runs before any SQL reaches a warehouse (definition imports, check_and_run, final
SELECTs); the MySQL connector's READ ONLY transaction is only defence in depth and does not
stop SELECT ... INTO OUTFILE or LOAD_FILE()."""

import pytest

from cteforge.compose.checks import ensure_query
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import PolicyError, RenderError, SqlParseError
from cteforge.parsing.analyze import parse
from cteforge.services._apply import ensure_read_only

MY = Dialect.MYSQL


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT a INTO @v FROM t",
        "SELECT * FROM t FOR UPDATE",
        "SELECT * FROM t FOR SHARE",
        "SELECT * FROM t LOCK IN SHARE MODE",
        "SELECT * FROM (SELECT a FROM t FOR UPDATE) AS q",
    ],
)
def test_select_into_and_row_locks_are_refused(sql):
    with pytest.raises(RenderError, match="not read-only"):
        ensure_query(parse(sql, MY), "query")


@pytest.mark.parametrize(
    "function",
    [
        "LOAD_FILE('/etc/passwd')",
        "SLEEP(100)",
        "BENCHMARK(100000000, MD5('x'))",
        "GET_LOCK('cteforge', 10)",
        "RELEASE_LOCK('cteforge')",
        "RELEASE_ALL_LOCKS()",
        "MASTER_POS_WAIT('log', 4)",
        "SOURCE_POS_WAIT('log', 4)",
        "WAIT_FOR_EXECUTED_GTID_SET('uuid:1-5')",
        "sys_exec('id')",
        "sys_eval('id')",
    ],
)
def test_mysql_side_effect_functions_are_refused(function):
    with pytest.raises(RenderError, match="read-only"):
        ensure_query(parse(f"SELECT {function} AS x FROM t", MY), "query")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT a FROM t WHERE b = 'x' LIMIT 10",
        "SELECT /*+ NO_INDEX_MERGE(t) */ a FROM t",
        "WITH x AS (SELECT a FROM t) SELECT a FROM x",
        "SELECT DATE_FORMAT(ts, '%Y-%m-01') AS m, COUNT(*) FROM t GROUP BY m",
    ],
)
def test_plain_selects_pass(sql):
    ensure_query(parse(sql, MY), "query")


@pytest.mark.parametrize(
    ("sql", "error"),
    [
        ("SELECT * FROM t INTO OUTFILE '/var/lib/mysql-files/x'", SqlParseError),
        ("SELECT a FROM t INTO DUMPFILE '/tmp/x'", SqlParseError),
        ("SELECT a, b INTO @v, @w FROM t", SqlParseError),
        ("SELECT a FROM t /*! INTO OUTFILE '/tmp/x' */", SqlParseError),
        ("SELECT /*+ MAX_EXECUTION_TIME(999999) */ a FROM t", SqlParseError),
        ("SELECT a INTO @v FROM t", PolicyError),
        ("SELECT LOAD_FILE('/etc/passwd') AS f", PolicyError),
    ],
)
def test_definition_sql_is_refused_before_any_check_runs(sql, error):
    """services._apply.ensure_read_only: what every proposed definition passes first."""
    with pytest.raises(error):
        ensure_read_only(sql, MY)
