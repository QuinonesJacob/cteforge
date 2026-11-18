"""ClickHouse templates and rendering (ADR 0025): `{name: Type}` placeholders round-trip,
literal rendering, grains rebuilt in the SQL's own function family, and the trace-shaped
definition in tests/fixtures/clickhouse (tool-call error rate; also used by the live tests).

Golden files (tests/golden/trace_tool_errors*.clickhouse.sql) update only deliberately:
    UPDATE_GOLDEN=1 uv run pytest tests/unit/templating/test_clickhouse_templates.py
"""

import os
from pathlib import Path

import pytest

from cteforge.domain.codec import annotations_from_yaml
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import AnnotationError, SqlParseError
from cteforge.domain.issues import Severity
from cteforge.domain.models import Annotation, Definition
from cteforge.domain.variables import Role, VariableType
from cteforge.parsing.analyze import analyze, find_grains, parse
from cteforge.templating.render import render, render_tree, template_slots
from cteforge.templating.roundtrip import roundtrip_check
from cteforge.templating.template import apply_annotations

CH = Dialect.CLICKHOUSE
TESTS = Path(__file__).resolve().parents[2]
FIXTURES = TESTS / "fixtures" / "clickhouse"
GOLDEN = TESTS / "golden"

# What system.columns reports for the trace tables (tests/fixtures/clickhouse/live_setup.py).
TRACE_SCHEMA = {
    "agent_traces.traces": {
        "trace_id": "UInt64",
        "started_at": "DateTime64(3, 'UTC')",
        "model": "LowCardinality(String)",
        "tags": "Array(LowCardinality(String))",
        "attrs": "Map(String, String)",
    },
    "agent_traces.turns": {
        "turn_id": "UInt64",
        "trace_id": "UInt64",
        "idx": "UInt8",
        "role": "Enum8('user' = 1, 'assistant' = 2, 'tool' = 3)",
        "text": "String",
        "started_at": "DateTime64(3, 'UTC')",
    },
    "agent_traces.tool_calls": {
        "call_id": "UInt64",
        "turn_id": "UInt64",
        "tool_name": "LowCardinality(String)",
        "status": "Enum8('ok' = 1, 'error' = 2)",
        "latency_ms": "UInt32",
        "called_at": "DateTime64(3, 'UTC')",
        "args": "String",
    },
}


def build(sql: str, annotations: list[Annotation], schema=None) -> Definition:
    annotated = apply_annotations(sql, CH, analyze(sql, CH, schema), annotations)
    assert [i for i in annotated.issues if i.severity is Severity.ERROR] == []
    return Definition(
        id="x",
        title="x",
        source_dialect=CH,
        template=annotated.template,
        variables=annotated.variables,
    )


def trace_definition() -> Definition:
    sql = (FIXTURES / "trace_tool_errors.sql").read_text()
    meta = annotations_from_yaml((FIXTURES / "trace_tool_errors.annotations.yml").read_text())
    annotated = apply_annotations(sql, CH, analyze(sql, CH, TRACE_SCHEMA), meta.variables)
    assert [i for i in annotated.issues if i.severity is Severity.ERROR] == []
    return Definition(
        id=meta.id,
        title=meta.title,
        source_dialect=CH,
        template=annotated.template,
        variables=annotated.variables,
    )


# ── Placeholders ────────────────────────────────────────────────────────────────────


def test_placeholders_are_typed_query_parameters_that_parse_back():
    sql = (
        "SELECT * FROM t WHERE t.model IN ('a', 'b') AND t.tool = 'x' AND t.n > 5 "
        "AND t.d >= toDate('2026-01-01') AND t.d < toDate('2026-02-01') AND t.ok = TRUE"
    )
    schema = {"t": {"model": "String", "tool": "String", "n": "UInt64", "d": "Date", "ok": "Bool"}}
    definition = build(
        sql,
        [
            Annotation(name="models", type=VariableType.TEXT_LIST, literals=["main.L1", "main.L2"]),
            Annotation(name="tool", type=VariableType.TEXT, literals=["main.L3"]),
            Annotation(name="min_n", type=VariableType.NUMBER, literals=["main.L4"]),
            Annotation(
                name="period", type=VariableType.DATE_RANGE, literals=["main.L5", "main.L6"]
            ),
            Annotation(name="ok", type=VariableType.BOOLEAN, literals=["main.L7"]),
        ],
        schema,
    )
    template = definition.template
    for written in (
        "IN ({models: String})",
        "t.tool = {tool: String}",
        "t.n > {min_n: Float64}",
        "toDate({period__start: DATE})",
        "t.ok = {ok: Bool}",
    ):
        assert written in template
    names = sorted(name for _, name in template_slots(parse(template, CH)))
    assert names == ["min_n", "models", "ok", "period__end", "period__start", "tool"]
    assert roundtrip_check(sql, definition).identical
    rendered = render(definition, {"models": ["c"], "min_n": 7, "ok": False}).sql
    assert "{" not in rendered
    assert "IN ('c')" in rendered and "t.n > 7" in rendered and "t.ok = FALSE" in rendered


def test_query_parameters_in_the_trusted_sql_are_refused_at_import():
    sql = "SELECT * FROM t WHERE t.model = {model:String} AND t.tool = 'x'"
    with pytest.raises(SqlParseError, match="query parameter"):
        apply_annotations(sql, CH, analyze("SELECT 1", CH), [])


# ── Literals ────────────────────────────────────────────────────────────────────────


def test_native_literals_render_exactly_and_hostile_text_stays_one_literal():
    sql = (
        "SELECT * FROM t WHERE t.ts >= CAST('2026-01-01' AS DateTime64(3, 'UTC')) "
        "AND t.d < CAST('2026-04-01' AS Date) AND t.dt >= '2026-01-01'::DateTime('UTC') "
        "AND t.n > 18446744073709551615 AND t.amount > 1000.50 AND t.ok = TRUE AND t.name = 'x'"
    )
    schema = {
        "t": {
            "ts": "DateTime64(3, 'UTC')",
            "d": "Date",
            "dt": "DateTime('UTC')",
            "n": "UInt64",
            "amount": "Decimal(18, 2)",
            "ok": "Bool",
            "name": "LowCardinality(String)",
        }
    }
    definition = build(
        sql,
        [
            Annotation(name="since", type=VariableType.DATE, literals=["main.L1"]),
            Annotation(name="until", type=VariableType.DATE, literals=["main.L2"]),
            Annotation(name="dt_since", type=VariableType.DATE, literals=["main.L3"]),
            Annotation(name="max_n", type=VariableType.NUMBER, literals=["main.L4"]),
            Annotation(name="min_amount", type=VariableType.NUMBER, literals=["main.L5"]),
            Annotation(name="ok", type=VariableType.BOOLEAN, literals=["main.L6"]),
            Annotation(name="who", type=VariableType.TEXT, literals=["main.L7"]),
        ],
        schema,
    )
    assert roundtrip_check(sql, definition).identical
    unchanged = render(definition, {}).sql
    assert "18446744073709551615" in unchanged and "1000.50" in unchanged
    hostile = "o\\'; DROP TABLE t; --"
    out = render(
        definition,
        {
            "since": "2026-02-01",
            "until": "2026-03-01",
            "dt_since": "2026-02-02",
            "max_n": 5,
            "min_amount": -1.5,
            "ok": False,
            "who": hostile,
        },
    )
    assert not out.transpiled
    assert "t.ts >= CAST('2026-02-01' AS DateTime64(3, 'UTC'))" in out.sql
    assert "t.d < CAST('2026-03-01' AS DATE)" in out.sql
    assert "t.dt >= CAST('2026-02-02' AS DateTime('UTC'))" in out.sql
    assert "t.n > 5" in out.sql and "t.amount > -1.5" in out.sql and "t.ok = FALSE" in out.sql
    # ClickHouse reads \' as an escaped quote: the backslash is doubled, the quote escaped.
    assert "t.name = 'o\\\\''; DROP TABLE t; --'" in out.sql
    reparsed = analyze(out.sql, CH)
    assert reparsed.literals[-1].value == hostile
    assert len(reparsed.literals) == 7  # still seven literals: nothing escaped the string


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        (Dialect.DUCKDB, "CAST('2026-02-01' AS TIMESTAMPTZ)"),
        (Dialect.SNOWFLAKE, "CAST('2026-02-01' AS TIMESTAMPTZ)"),
        (Dialect.BIGQUERY, "CAST('2026-02-01' AS TIMESTAMP)"),
    ],
)
def test_zoned_datetime64_transpiles_as_zone_aware(target, expected):
    sql = "SELECT * FROM t WHERE t.ts >= CAST('2026-01-01' AS DateTime64(3, 'UTC'))"
    definition = build(
        sql,
        [Annotation(name="since", type=VariableType.DATE, literals=["main.L1"])],
        {"t": {"ts": "DateTime64(3, 'UTC')"}},
    )
    out = render(definition, {"since": "2026-02-01"}, target)
    assert out.transpiled and expected in out.sql


def test_naive_datetime_transpiles_as_naive():
    sql = "SELECT * FROM t WHERE t.ts >= CAST('2026-01-01' AS DateTime64(3))"
    definition = build(
        sql,
        [Annotation(name="since", type=VariableType.DATE, literals=["main.L1"])],
        {"t": {"ts": "DateTime64(3)"}},
    )
    assert "CAST('2026-01-01' AS TIMESTAMP)" in render(definition, {}, Dialect.DUCKDB).sql


# ── Grains ──────────────────────────────────────────────────────────────────────────


def grain(grain_id: str = "main.G1") -> Annotation:
    return Annotation(
        name="time_grain", type=VariableType.GRAIN, literals=[grain_id], role=Role.CHANGEABLE
    )


@pytest.mark.parametrize(
    ("original", "unit", "expected"),
    [
        ("toStartOfMonth(t.ts, 'UTC')", "week", "toMonday(t.ts, 'UTC')"),
        ("toStartOfMonth(t.ts, 'UTC')", "day", "toStartOfDay(t.ts, 'UTC')"),
        ("toMonday(t.ts)", "quarter", "toStartOfQuarter(t.ts)"),
        ("toStartOfWeek(t.ts, 1)", "month", "toStartOfMonth(t.ts)"),
        ("toStartOfWeek(t.ts, 3, 'UTC')", "year", "toStartOfYear(t.ts, 'UTC')"),
        ("date_trunc('month', t.ts)", "week", "date_trunc('week', t.ts)"),
        (
            "toStartOfInterval(t.ts, INTERVAL 1 MONTH)",
            "quarter",
            "toStartOfInterval(t.ts, INTERVAL '1' QUARTER)",
        ),
        (
            "toStartOfInterval(t.ts, INTERVAL 1 DAY, 'UTC')",
            "week",
            "toStartOfInterval(t.ts, INTERVAL '1' WEEK, 'UTC')",
        ),
    ],
)
def test_grain_rebuilds_in_the_sql_s_own_function_family(original, unit, expected):
    sql = f"SELECT {original} AS p, count() AS n FROM t GROUP BY p"
    definition = build(sql, [grain()], {"t": {"ts": "DateTime64(3, 'UTC')"}})
    assert roundtrip_check(sql, definition).identical
    result = render(definition, {"time_grain": unit})
    assert f"{expected} AS p" in result.sql
    tree, _, _ = render_tree(definition, {"time_grain": unit})
    reparsed = parse(result.sql, CH)
    assert reparsed == tree
    assert [g.unit for g in find_grains(reparsed, CH)] == [unit]


def test_sunday_weeks_are_not_a_changeable_grain():
    sql = "SELECT toStartOfWeek(t.ts) AS w FROM t"
    analysis = analyze(sql, CH)
    assert analysis.grains[0].unit == "week(sunday)"
    with pytest.raises(AnnotationError):
        apply_annotations(sql, CH, analysis, [grain()])


# ── The trace definition (tests/fixtures/clickhouse) ────────────────────────────────


def test_trace_definition_same_query_back():
    definition = trace_definition()
    sql = (FIXTURES / "trace_tool_errors.sql").read_text()
    assert roundtrip_check(sql, definition).identical
    assert [v.name for v in definition.variables] == [
        "failed_status",
        "period",
        "models",
        "required_tag",
        "time_grain",
    ]
    period = definition.variable("period")
    assert period.column == "agent_traces.traces.started_at"
    assert definition.variable("required_tag").column == "agent_traces.traces.tags"


@pytest.mark.parametrize(
    ("name", "values"),
    [
        ("trace_tool_errors", {}),
        (
            "trace_tool_errors_weekly",
            {
                "period": {"start": "2026-01-05", "end": "2026-01-19"},
                "models": ["claude-haiku-4"],
                "required_tag": "eval",
                "time_grain": "week",
            },
        ),
    ],
)
def test_trace_definition_golden(name, values):
    result = render(trace_definition(), values)
    path = GOLDEN / f"{name}.clickhouse.sql"
    if os.environ.get("UPDATE_GOLDEN") == "1":
        path.write_text(result.sql + "\n")
    assert path.exists(), f"Missing golden file {path.name}; create it with UPDATE_GOLDEN=1"
    assert result.sql + "\n" == path.read_text()
    assert not result.transpiled and result.issues == []
