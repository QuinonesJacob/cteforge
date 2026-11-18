"""MySQL parsing (ADR 0028): type mapping, SQL the server would run differently from how
cteforge reads it (executable comments, setting hints), and the reading normalizations that
keep same-query-back exact."""

import pytest

from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import SqlParseError
from cteforge.domain.variables import VariableType as V
from cteforge.parsing.analyze import generate, parse
from cteforge.parsing.types import compatible_variable_types

MY = Dialect.MYSQL


@pytest.mark.parametrize(
    ("type_str", "expected"),
    [
        ("tinyint(1)", {V.NUMBER, V.BOOLEAN}),  # BOOL / BOOLEAN columns
        ("bool", {V.BOOLEAN}),
        ("tinyint(4)", {V.NUMBER}),
        ("tinyint unsigned", {V.NUMBER}),
        ("int unsigned", {V.NUMBER}),
        ("bigint(20) unsigned", {V.NUMBER}),
        ("decimal(10,2)", {V.NUMBER}),
        ("decimal(10,2) unsigned", {V.NUMBER}),
        ("double", {V.NUMBER}),
        ("year", {V.NUMBER}),
        ("date", {V.DATE, V.DATE_RANGE}),
        ("datetime", {V.DATE, V.DATE_RANGE}),
        ("datetime(6)", {V.DATE, V.DATE_RANGE}),
        ("timestamp", {V.DATE, V.DATE_RANGE}),
        ("varchar(255)", {V.TEXT, V.TEXT_LIST}),
        ("char(2)", {V.TEXT, V.TEXT_LIST}),
        ("longtext", {V.TEXT, V.TEXT_LIST}),
        ("enum('paid','new')", {V.TEXT, V.TEXT_LIST}),
        ("set('a','b')", {V.TEXT, V.TEXT_LIST}),
        ("json", set()),
        ("blob", set()),
        ("longblob", set()),
        ("varbinary(16)", set()),
        ("binary(16)", set()),
        ("geometry", set()),
        ("point", set()),
    ],
)
def test_mysql_types(type_str, expected):
    assert compatible_variable_types(type_str, MY) == expected


def test_mysql_only_mappings_do_not_leak_into_other_dialects():
    assert compatible_variable_types("tinyint(1)", Dialect.DUCKDB) == {V.NUMBER}
    assert compatible_variable_types("enum('a')", Dialect.POSTGRES) == set()


def test_timestamp_is_zone_aware_and_datetime_naive():
    from cteforge.parsing.types import data_type

    assert data_type("timestamp", MY).is_type("timestamptz")
    assert data_type("datetime", MY).is_type("datetime")


# ── SQL the server would run differently ────────────────────────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1 FROM t /*! INTO OUTFILE '/tmp/x' */",
        "SELECT 1 /*!50000, LOAD_FILE('/etc/passwd') */ FROM t",
        "SELECT /*!*/ 1",
        "SELECT 1 /*M! , SLEEP(5) */",
        "SELECT 1 FROM t;/*! DROP TABLE t */",
        # A line comment starting with "!" would be written back as /*! ... */.
        "SELECT a FROM t #!, SLEEP(5)",
    ],
)
def test_executable_comments_are_refused(sql):
    with pytest.raises(SqlParseError, match="executable comment"):
        parse(sql, MY)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT /*+ MAX_EXECUTION_TIME(100000000) */ a FROM t",
        "SELECT /*+ max_execution_time(1) */ a FROM t",
        "SELECT /*+ SET_VAR(max_execution_time = 100000) */ a FROM t",
        "SELECT /*+ SET_VAR(sql_mode = 'NO_BACKSLASH_ESCAPES') */ a FROM t",
        "SELECT * FROM (SELECT /*+ SET_VAR(max_execution_time=9) */ 1 AS a) AS q",
        "WITH x AS (SELECT /*+ MAX_EXECUTION_TIME(9) */ 1 AS a) SELECT * FROM x",
        "SELECT a #+ MAX_EXECUTION_TIME(1)\nFROM t",
    ],
)
def test_setting_hints_are_refused(sql):
    with pytest.raises(SqlParseError, match="MAX_EXECUTION_TIME or SET_VAR"):
        parse(sql, MY)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT /*+ NO_INDEX_MERGE(t) */ a FROM t",
        "SELECT a FROM t WHERE b = '/*! SLEEP(5) */'",
        "SELECT a FROM t WHERE b = 'x\\' /*! SLEEP(5) */'",
        "SELECT a FROM t -- tail /*! SLEEP(5) */",
        "SELECT a FROM t # tail /*! SLEEP(5) */",
        "SELECT a /* plain comment */ FROM t",
    ],
)
def test_plain_comments_hints_and_strings_are_allowed(sql):
    parse(sql, MY)


def test_executable_comments_are_only_a_mysql_concern():
    parse("SELECT 1 /*! x */", Dialect.DUCKDB)


def test_comments_written_back_cannot_open_or_close_a_comment():
    """sqlglot rewrites line comments as /* */ and breaks up `*/` and `/*` inside them, so a
    line comment cannot smuggle SQL or an executable comment into rendered SQL."""
    sql = "SELECT 1 AS a -- x */ , SLEEP(5) /* y\nFROM t"
    out = generate(parse(sql, MY), MY, pretty=False)
    assert out == "SELECT 1 AS a /* x * / , SLEEP(5) / * y */ FROM t"
    assert parse(out, MY) == parse("SELECT 1 AS a FROM t", MY)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM t INTO OUTFILE '/tmp/x'",
        "SELECT * INTO OUTFILE '/tmp/x' FROM t",
        "SELECT a FROM t INTO DUMPFILE '/tmp/x'",
        "SELECT 1 UNION SELECT 2 INTO OUTFILE '/tmp/x'",
        "SELECT * FROM t INTO OUTFILE '/tmp/x' FIELDS TERMINATED BY ','",
    ],
)
def test_into_outfile_and_dumpfile_do_not_parse(sql):
    """sqlglot 30.31.0 cannot read INTO OUTFILE / DUMPFILE, so such SQL is refused before
    anything runs; SELECT ... INTO @var parses and is refused by compose.checks."""
    with pytest.raises(SqlParseError):
        parse(sql, MY)


def test_backslash_escaped_quote_does_not_end_a_string():
    """MySQL reads \\' as a quote inside a string: an unclosed-quote message points at the
    string that really is unclosed."""
    with pytest.raises(SqlParseError, match="column 21"):
        parse("SELECT 'a\\'b' AS x, 'c FROM t", MY)


# ── Reading normalizations (same query back) ────────────────────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT DATE_SUB(DATE(ts), INTERVAL WEEKDAY(ts) DAY) FROM t",
        "SELECT MAKEDATE(YEAR(ts), 1) + INTERVAL QUARTER(ts) - 1 QUARTER FROM t",
        "SELECT * FROM t WHERE d = TIMESTAMP '2026-01-01 10:00:00'",
        "SELECT * FROM t WHERE d >= CURDATE() - INTERVAL 7 DAY",
    ],
)
def test_written_back_mysql_reads_as_the_same_tree(sql):
    tree = parse(sql, MY)
    assert parse(generate(tree, MY), MY) == tree


def test_parenthesized_interval_count_reads_like_the_bare_one():
    bare = parse("SELECT ts - INTERVAL WEEKDAY(ts) DAY FROM t", MY)
    assert parse("SELECT ts - INTERVAL (WEEKDAY(ts)) DAY FROM t", MY) == bare


def test_timestamp_literal_is_written_as_a_literal():
    out = generate(parse("SELECT TIMESTAMP '2026-01-01 10:00:00' AS t", MY), MY, pretty=False)
    assert out == "SELECT TIMESTAMP '2026-01-01 10:00:00' AS t"
    computed = generate(parse("SELECT TIMESTAMP(d) AS t FROM x", MY), MY, pretty=False)
    assert computed == "SELECT TIMESTAMP(d) AS t FROM x"
