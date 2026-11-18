"""ClickHouse dialect in parsing (ADR 0025): types, AI-trace literal shapes, grains, query
parameters, clauses kept exactly, two-part names. Checked against sqlglot 30.31.0."""

import pytest

from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import SqlParseError
from cteforge.domain.variables import VariableType
from cteforge.parsing.analyze import (
    LiteralContext,
    analyze,
    generate,
    parse,
    referenced_tables,
    transpile,
)
from cteforge.parsing.types import compatible_variable_types

CH = Dialect.CLICKHOUSE
DATE = {VariableType.DATE, VariableType.DATE_RANGE}
TEXT = {VariableType.TEXT, VariableType.TEXT_LIST}
NUMBER = {VariableType.NUMBER}

SCHEMA = {
    "agent.traces": {
        "trace_id": "UUID",
        "started_at": "DateTime64(3, 'UTC')",
        "day": "Date",
        "model": "LowCardinality(String)",
        "tags": "Array(LowCardinality(String))",
        "tool_names": "Array(String)",
        "attrs": "Map(String, String)",
        "raw": "String",
        "tokens": "UInt64",
        "ok": "Bool",
    }
}


def literals(sql: str, schema=SCHEMA) -> list[tuple]:
    analysis = analyze(sql, CH, schema)
    return [
        (
            lit.value,
            lit.operator,
            lit.column.column if lit.column else None,
            lit.compared_type,
        )
        for lit in analysis.literals
    ]


# ── Types ───────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("type_str", "expected"),
    [
        ("Nullable(String)", TEXT),
        ("LowCardinality(String)", TEXT),
        ("LowCardinality(Nullable(String))", TEXT),
        ("LowCardinality(Nullable(FixedString(2)))", TEXT),
        ("FixedString(16)", TEXT),
        ("Enum8('ok' = 1, 'error' = 2)", TEXT),
        ("Enum16('a' = 1)", TEXT),
        ("UUID", TEXT),
        ("Date", DATE),
        ("Date32", DATE),
        ("DateTime", DATE),
        ("DateTime('UTC')", DATE),
        ("DateTime64(3)", DATE),
        ("DateTime64(6, 'Europe/Berlin')", DATE),
        ("Nullable(DateTime64(3, 'UTC'))", DATE),
        ("Int8", NUMBER),
        ("Int256", NUMBER),
        ("UInt8", NUMBER),
        ("UInt64", NUMBER),
        ("UInt256", NUMBER),
        ("Float32", NUMBER),
        ("Float64", NUMBER),
        ("Decimal(18, 4)", NUMBER),
        ("Decimal64(4)", NUMBER),
        ("SimpleAggregateFunction(sum, UInt64)", NUMBER),
        ("Bool", {VariableType.BOOLEAN}),
        ("Nullable(Bool)", {VariableType.BOOLEAN}),
        # Not filterable by one typed value.
        ("Array(String)", set()),
        ("Map(String, String)", set()),
        ("Tuple(a String, b Int64)", set()),
        ("JSON", set()),
        ("Object('json')", set()),
        ("Nested(a String, b Int64)", set()),
        ("AggregateFunction(uniq, String)", set()),
    ],
)
def test_clickhouse_types(type_str, expected):
    assert compatible_variable_types(type_str, CH) == expected


def test_clickhouse_only_text_types_stay_unknown_elsewhere():
    assert compatible_variable_types("uuid", Dialect.POSTGRES) == set()


# ── AI-trace literal shapes ─────────────────────────────────────────────────────────


def test_has_is_a_filter_on_the_array_elements():
    assert literals("SELECT * FROM agent.traces AS t WHERE has(t.tool_names, 'search')") == [
        ("search", "HAS", "tool_names", "String")
    ]


def test_has_any_and_has_all_are_list_shapes_like_any():
    sql = (
        "SELECT * FROM agent.traces AS t WHERE hasAny(t.tags, ['a', 'b']) "
        "AND hasAll(t.tool_names, ['x'])"
    )
    assert literals(sql) == [
        ("a", "HAS ANY", "tags", "LowCardinality(String)"),
        ("b", "HAS ANY", "tags", "LowCardinality(String)"),
        ("x", "HAS ALL", "tool_names", "String"),
    ]
    analysis = analyze(sql, CH, SCHEMA)
    assert all(lit.in_list is None for lit in analysis.literals)  # not IN groups


def test_array_exists_lambda_compares_the_array_elements():
    sql = (
        "SELECT * FROM agent.traces AS t WHERE arrayExists(x -> x = 'web_search', t.tool_names) "
        "AND arrayExists(x -> lower(x) = 'code', t.tool_names)"
    )
    got = literals(sql)
    assert got[0] == ("web_search", "=", "tool_names", "String")
    assert got[1][:3] == ("code", "=", "tool_names")
    assert compatible_variable_types(got[1][3], CH) == TEXT  # lower(x): Nullable(String)


def test_array_filter_and_all_lambdas():
    sql = (
        "SELECT * FROM agent.traces AS t WHERE length(arrayFilter(x -> x = 'a', t.tags)) > 0 "
        "AND arrayAll(y -> y <> 'b', t.tool_names)"
    )
    analysis = analyze(sql, CH, SCHEMA)
    got = [(lit.value, lit.operator, lit.column.column, lit.context) for lit in analysis.literals]
    assert got == [
        ("a", "=", "tags", []),
        ("0", ">", "tags", []),
        ("b", "<>", "tool_names", [LiteralContext.NEGATED]),
    ]


def test_json_and_map_access_are_compared_as_strings():
    sql = (
        "SELECT * FROM agent.traces AS t WHERE JSONExtractString(t.raw, 'model') = 'claude-x' "
        "AND t.attrs['env'] = 'prod'"
    )
    assert literals(sql) == [
        ("claude-x", "=", "raw", "String"),
        ("prod", "=", "attrs", "String"),
    ]


def test_array_join_names_resolve_to_the_array_column():
    sql = (
        "SELECT t.trace_id, tool FROM agent.traces AS t ARRAY JOIN t.tool_names AS tool "
        "WHERE tool = 'search'"
    )
    assert literals(sql) == [("search", "=", "tool_names", "String")]


def test_interval_lookback_value_is_wrapped():
    analysis = analyze(
        "SELECT * FROM agent.traces AS t WHERE t.started_at >= now() - INTERVAL 7 DAY", CH, SCHEMA
    )
    (lit,) = analysis.literals
    assert (lit.value, lit.operator, lit.column.column) == ("7", ">=", "started_at")
    assert lit.context == [LiteralContext.VALUE_WRAPPED]


def test_to_date_between_is_a_range_on_a_date():
    sql = (
        "SELECT * FROM agent.traces AS t "
        "WHERE toDate(t.started_at) BETWEEN '2026-01-01' AND '2026-02-01'"
    )
    analysis = analyze(sql, CH, SCHEMA)
    assert [(lit.operator, lit.compared_type) for lit in analysis.literals] == [
        ("BETWEEN_LOW", "DATE"),
        ("BETWEEN_HIGH", "DATE"),
    ]
    assert [r.shape.value for r in analysis.ranges] == ["inclusive"]


def test_conversion_options_are_not_values_and_ranges_pair():
    sql = (
        "SELECT * FROM agent.traces AS t "
        "WHERE t.started_at >= toDateTime64('2026-01-01 00:00:00', 3, 'UTC') "
        "AND t.started_at < toDateTime64('2026-02-01 00:00:00', 3, 'UTC')"
    )
    analysis = analyze(sql, CH, SCHEMA)
    assert [lit.value for lit in analysis.literals] == [
        "2026-01-01 00:00:00",
        "2026-02-01 00:00:00",
    ]
    assert analysis.literals[0].wrapper_sql == "toDateTime64(?, 3, 'UTC')"
    assert [r.shape.value for r in analysis.ranges] == ["half_open"]


def test_in_like_ilike_match():
    sql = (
        "SELECT * FROM agent.traces AS t WHERE t.model IN ('a', 'b') AND t.raw LIKE '%err%' "
        "AND t.raw ILIKE 'x%' AND match(t.model, '^claude')"
    )
    analysis = analyze(sql, CH, SCHEMA)
    assert [(lit.operator, lit.in_list) for lit in analysis.literals] == [
        ("IN", "main.IN1"),
        ("IN", "main.IN1"),
        ("LIKE", None),
        ("ILIKE", None),
        ("MATCH", None),
    ]


def test_numbers_booleans_and_uint64_range():
    sql = "SELECT * FROM agent.traces AS t WHERE t.tokens > 18446744073709551615 AND t.ok = TRUE"
    assert literals(sql) == [
        ("18446744073709551615", ">", "tokens", "UInt64"),
        ("TRUE", "=", "ok", "Bool"),
    ]


@pytest.mark.parametrize(
    ("dialect", "sql"),
    [
        (Dialect.DUCKDB, "SELECT * FROM t WHERE list_contains(t.tags, 'a')"),
        (Dialect.SNOWFLAKE, "SELECT * FROM t WHERE ARRAY_CONTAINS('a'::VARIANT, t.tags)"),
        (Dialect.POSTGRES, "SELECT * FROM t WHERE t.name ~ '^a'"),
        (Dialect.DATABRICKS, "SELECT * FROM t WHERE exists(t.tags, x -> x = 'a')"),
    ],
)
def test_clickhouse_shapes_do_not_add_literal_ids_in_other_dialects(dialect, sql):
    """Stored literal IDs in other dialects must not shift (ADR 0025)."""
    assert analyze(sql, dialect).literals == []


# ── Query parameters ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM agent.traces WHERE model = {model:String}",
        "SELECT * FROM agent.traces WHERE started_at >= {since:DateTime64(3)}",
        "SELECT * FROM {tbl:Identifier}",
    ],
)
def test_query_parameters_in_trusted_sql_are_refused_clearly(sql):
    with pytest.raises(SqlParseError, match="ClickHouse query parameter .* literal"):
        analyze(sql, CH)


def test_braces_inside_strings_are_just_text():
    analysis = analyze("SELECT * FROM agent.traces AS t WHERE t.raw = '{x:String}'", CH, SCHEMA)
    assert [lit.value for lit in analysis.literals] == ["{x:String}"]


# ── Grains ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("expr", "unit", "written"),
    [
        ("toStartOfDay(t.started_at)", "day", "toStartOfDay(t.started_at)"),
        ("toMonday(t.started_at)", "week", "toMonday(t.started_at)"),
        ("toStartOfMonth(t.started_at)", "month", "toStartOfMonth(t.started_at)"),
        ("toStartOfQuarter(t.started_at)", "quarter", "toStartOfQuarter(t.started_at)"),
        ("toStartOfYear(t.started_at)", "year", "toStartOfYear(t.started_at)"),
        ("toStartOfMonth(t.started_at, 'UTC')", "month", "toStartOfMonth(t.started_at, 'UTC')"),
        ("toStartOfHour(t.started_at)", "hour", "toStartOfHour(t.started_at)"),
        ("date_trunc('month', t.started_at)", "month", "date_trunc('month', t.started_at)"),
        ("dateTrunc('week', t.started_at)", "week", "dateTrunc('week', t.started_at)"),
        # toStartOfWeek: mode 0 (default) / even = Sunday; odd = Monday (native week).
        ("toStartOfWeek(t.started_at)", "week(sunday)", "toStartOfWeek(t.started_at)"),
        ("toStartOfWeek(t.started_at, 0)", "week(sunday)", "toStartOfWeek(t.started_at, 0)"),
        ("toStartOfWeek(t.started_at, 1)", "week", "toStartOfWeek(t.started_at, 1)"),
        ("toStartOfWeek(t.started_at, 3, 'UTC')", "week", "toStartOfWeek(t.started_at, 3, 'UTC')"),
        (
            "toStartOfInterval(t.started_at, INTERVAL 1 MONTH)",
            "month",
            "toStartOfInterval(t.started_at, INTERVAL '1' MONTH)",
        ),
        (
            "toStartOfInterval(t.started_at, INTERVAL 15 MINUTE)",
            "'15' minute",
            "toStartOfInterval(t.started_at, INTERVAL '15' MINUTE)",
        ),
    ],
)
def test_grain_candidates(expr, unit, written):
    analysis = analyze(f"SELECT {expr} AS p FROM agent.traces AS t", CH, SCHEMA)
    (grain,) = analysis.grains
    assert (grain.unit, grain.sql, grain.column_sql) == (unit, written, "t.started_at")
    assert grain.column.column == "started_at" and grain.output == "p"


def test_native_truncation_names_are_written_back_as_written():
    sql = "SELECT toStartOfMonth(ts) AS m, toMonday(ts) AS w, toStartOfDay(ts, 'UTC') AS d FROM t"
    out = transpile(sql, CH, CH)
    assert "toStartOfMonth(ts) AS m" in out
    assert "toMonday(ts) AS w" in out
    assert "toStartOfDay(ts, 'UTC') AS d" in out
    assert parse(out, CH) == parse(sql, CH)
    # Other targets get sqlglot's own spelling; the tree itself is unchanged.
    assert "DATE_TRUNC('MONTH', ts)" in transpile(sql, CH, Dialect.DUCKDB)
    tree = parse(sql, CH)
    generate(tree, CH)
    assert tree.selects[0].unalias().meta["clickhouse_name"] == "toStartOfMonth"


# ── Clauses kept exactly, names ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM agent.traces FINAL WHERE model = 'a'",
        "SELECT * FROM agent.traces SAMPLE 0.1 WHERE model = 'a'",
        "SELECT * FROM agent.traces SAMPLE 1 / 10 OFFSET 1 / 2",
        "SELECT * FROM agent.traces PREWHERE model = 'a' WHERE started_at > '2026-01-01'",
        "SELECT t.trace_id, tool FROM agent.traces AS t ARRAY JOIN t.tool_names AS tool",
        "SELECT t.trace_id, tool FROM agent.traces AS t LEFT ARRAY JOIN t.tool_names AS tool",
        "SELECT * FROM agent.traces ORDER BY started_at DESC LIMIT 3 BY model",
        "SELECT * FROM agent.traces WHERE model = 'a' SETTINGS max_threads = 4",
        "WITH 'claude' AS m SELECT * FROM agent.traces WHERE model = m",
        "WITH (SELECT max(started_at) FROM agent.traces) AS mx SELECT * FROM agent.traces "
        "WHERE started_at > mx - INTERVAL '1' DAY",
        "SELECT * FROM agent.traces WHERE trace_id GLOBAL IN (SELECT trace_id FROM agent.turns "
        "WHERE role = 'tool')",
        "SELECT * FROM agent.traces WHERE trace_id GLOBAL NOT IN "
        "(SELECT trace_id FROM agent.turns)",
        "SELECT quantile(0.95)(latency_ms) FROM agent.tool_calls",
    ],
)
def test_clickhouse_clauses_round_trip_exactly(sql):
    written = transpile(sql, CH, CH)
    assert " ".join(written.split()).replace("( ", "(").replace(" )", ")") == sql
    assert parse(written, CH) == parse(sql, CH)
    analyze(sql, CH, SCHEMA)  # analysis copes with every clause


def test_scalar_with_value_is_a_named_constant_not_a_filter_literal():
    analysis = analyze("WITH 'claude' AS m SELECT * FROM agent.traces WHERE model = m", CH, SCHEMA)
    assert analysis.literals == []


def test_two_part_names_are_database_dot_table():
    sql = "SELECT t.model FROM agent.traces AS t JOIN Agent.Turns AS u ON u.trace_id = t.trace_id"
    assert referenced_tables(sql, CH) == ["agent.traces", "agent.turns"]
    analysis = analyze("SELECT * FROM agent.traces AS t WHERE t.model = 'a'", CH, SCHEMA)
    assert analysis.literals[0].column.table == "agent.traces"
    assert analysis.literals[0].compared_type == "LowCardinality(String)"
