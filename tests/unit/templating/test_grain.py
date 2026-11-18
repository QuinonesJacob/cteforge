"""grain variable (M1.5): annotate a truncation, render other units by rebuilding the node.

The template keeps the truncation exactly as written (ADR 0011: grain is never a value
placeholder); render rebuilds the node named by the grain ID for another unit.
"""

import re
from pathlib import Path

import pytest

from cteforge.domain.codec import annotations_from_yaml
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import AnnotationError, RenderError, VariableValueError
from cteforge.domain.issues import IssueCode, Severity
from cteforge.domain.models import Annotation, Definition
from cteforge.domain.variables import Grain, Role, VariableType
from cteforge.parsing.analyze import analyze, find_grains, parse
from cteforge.templating.render import render, render_tree
from cteforge.templating.roundtrip import roundtrip_check
from cteforge.templating.template import apply_annotations

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"
SCHEMA = {"orders": {"id": "INTEGER", "created_at": "TIMESTAMP", "day": "DATE"}}

# Monthly orders since a date, written natively per dialect. G1 = the month truncation.
SOURCES = {
    Dialect.DUCKDB: (
        "SELECT DATE_TRUNC('month', o.created_at) AS m, COUNT(*) AS n FROM orders AS o "
        "WHERE o.created_at >= TIMESTAMP '2026-01-01' GROUP BY 1"
    ),
    Dialect.POSTGRES: (
        "SELECT date_trunc('month', o.created_at) AS m, COUNT(*) AS n FROM orders AS o "
        "WHERE o.created_at >= TIMESTAMP '2026-01-01' GROUP BY 1"
    ),
    Dialect.REDSHIFT: (
        "SELECT date_trunc('month', o.created_at) AS m, COUNT(*) AS n FROM orders AS o "
        "WHERE o.created_at >= '2026-01-01'::TIMESTAMP GROUP BY 1"
    ),
    Dialect.SNOWFLAKE: (
        "SELECT DATE_TRUNC(month, o.created_at) AS m, COUNT(*) AS n FROM orders AS o "
        "WHERE o.created_at >= '2026-01-01'::TIMESTAMP_NTZ GROUP BY 1"
    ),
    Dialect.BIGQUERY: (
        "SELECT DATETIME_TRUNC(o.created_at, MONTH) AS m, COUNT(*) AS n FROM orders AS o "
        "WHERE o.created_at >= DATETIME '2026-01-01' GROUP BY 1"
    ),
    Dialect.DATABRICKS: (
        "SELECT DATE_TRUNC('MONTH', o.created_at) AS m, COUNT(*) AS n FROM orders AS o "
        "WHERE o.created_at >= TIMESTAMP '2026-01-01' GROUP BY 1"
    ),
    Dialect.CLICKHOUSE: (
        "SELECT toStartOfMonth(o.created_at) AS m, count() AS n FROM orders AS o "
        "WHERE o.created_at >= toDateTime('2026-01-01 00:00:00') GROUP BY 1"
    ),
}

# The truncation each dialect renders for each grain (native syntax, native week).
EXPECTED = {
    Dialect.DUCKDB: "DATE_TRUNC('{U}', o.created_at)",
    Dialect.POSTGRES: "DATE_TRUNC('{U}', o.created_at)",
    Dialect.REDSHIFT: "DATE_TRUNC('{U}', o.created_at)",
    Dialect.SNOWFLAKE: "DATE_TRUNC('{U}', o.created_at)",
    Dialect.BIGQUERY: "DATETIME_TRUNC(o.created_at, {U})",
    Dialect.DATABRICKS: "DATE_TRUNC('{U}', o.created_at)",
    # ClickHouse's own function per grain (ADR 0025); week = toMonday (Monday).
    Dialect.CLICKHOUSE: {
        "DAY": "toStartOfDay(o.created_at)",
        "WEEK": "toMonday(o.created_at)",
        "MONTH": "toStartOfMonth(o.created_at)",
        "QUARTER": "toStartOfQuarter(o.created_at)",
        "YEAR": "toStartOfYear(o.created_at)",
    },
}


def expected(dialect: Dialect, unit: Grain) -> str:
    spelled = EXPECTED[dialect]
    key = unit.value.upper()
    return spelled[key] if isinstance(spelled, dict) else spelled.format(U=key)


def build(sql: str, dialect: Dialect, annotations: list[Annotation], schema=SCHEMA) -> Definition:
    analysis = analyze(sql, dialect, schema)
    annotated = apply_annotations(sql, dialect, analysis, annotations)
    return Definition(
        id="orders_by_grain",
        title="Orders by grain",
        source_dialect=dialect,
        template=annotated.template,
        variables=annotated.variables,
    )


def grain(*ids: str, name: str = "time_grain", **kw) -> Annotation:
    kw.setdefault("role", Role.CHANGEABLE)
    return Annotation(name=name, type=VariableType.GRAIN, literals=list(ids), **kw)


# ── Every dialect ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("dialect", list(Dialect))
def test_annotate_keeps_the_truncation_and_records_the_original_unit(dialect):
    definition = build(SOURCES[dialect], dialect, [grain("main.G1")])
    spec = definition.variable("time_grain")
    assert spec.type is VariableType.GRAIN
    assert spec.original_value == "month"
    assert spec.column == "orders.created_at" and spec.column_type == "TIMESTAMP"
    assert [(s.placeholder, s.literals, s.original_text) for s in spec.slots] == [
        ("time_grain", ["main.G1"], "month")
    ]
    # No placeholder: the stored template holds the truncation as written, and re-parsing
    # it in the source dialect finds the same grain ID with the same unit.
    tree = parse(definition.template, dialect)
    assert [(g.id, g.unit) for g in find_grains(tree, dialect)] == [("main.G1", "month")]


@pytest.mark.parametrize("dialect", list(Dialect))
def test_same_query_back_with_the_original_grain(dialect):
    definition = build(SOURCES[dialect], dialect, [grain("main.G1")])
    report = roundtrip_check(SOURCES[dialect], definition)
    assert report.identical, report.diff
    explicit = render_tree(definition, {"time_grain": "month"})[0]
    assert explicit == parse(SOURCES[dialect], dialect)


@pytest.mark.parametrize("unit", list(Grain))
@pytest.mark.parametrize("dialect", list(Dialect))
def test_render_every_grain_natively(dialect, unit):
    definition = build(SOURCES[dialect], dialect, [grain("main.G1")])
    result = render(definition, {"time_grain": unit.value})
    assert not result.transpiled and result.issues == []
    assert result.values == {"time_grain": unit.value}
    assert expected(dialect, unit) in result.sql
    # The rebuilt tree survives a sqlglot round trip: re-parsing the rendered SQL gives the
    # same tree and the requested unit back.
    tree, _, _ = render_tree(definition, {"time_grain": unit.value})
    reparsed = parse(result.sql, dialect)
    assert reparsed == tree
    assert [g.unit for g in find_grains(reparsed, dialect)] == [unit.value]


def test_bigquery_week_is_its_native_sunday_week_node():
    """BigQuery parses WEEK as WEEK(SUNDAY); the rebuilt node matches what it parses."""
    sql = "SELECT DATE_TRUNC(o.day, MONTH) AS m FROM orders AS o"
    definition = build(sql, Dialect.BIGQUERY, [grain("main.G1")])
    result = render(definition, {"time_grain": "week"})
    assert "DATE_TRUNC(o.day, WEEK)" in result.sql
    assert parse(result.sql, Dialect.BIGQUERY) == render_tree(definition, {"time_grain": "week"})[0]


def test_bigquery_original_week_round_trips():
    sql = "SELECT TIMESTAMP_TRUNC(o.created_at, WEEK) AS w FROM orders AS o"
    definition = build(sql, Dialect.BIGQUERY, [grain("main.G1")])
    assert definition.variable("time_grain").original_value == "week"
    assert roundtrip_check(sql, definition).identical
    assert "TIMESTAMP_TRUNC(o.created_at, YEAR)" in render(definition, {"time_grain": "year"}).sql


def test_duckdb_date_argument_keeps_its_date_truncation():
    sql = "SELECT DATE_TRUNC('month', CAST(o.created_at AS DATE)) AS m FROM orders AS o"
    definition = build(sql, Dialect.DUCKDB, [grain("main.G1")])
    result = render(definition, {"time_grain": "quarter"})
    assert "DATE_TRUNC('QUARTER', CAST(o.created_at AS DATE))" in result.sql
    assert (
        parse(result.sql, Dialect.DUCKDB) == render_tree(definition, {"time_grain": "quarter"})[0]
    )


def test_databricks_trunc_supports_quarter_but_not_day():
    sql = "SELECT TRUNC(o.day, 'MM') AS m FROM orders AS o"
    definition = build(sql, Dialect.DATABRICKS, [grain("main.G1")])
    assert roundtrip_check(sql, definition).identical
    assert "TRUNC(o.day, 'QUARTER')" in render(definition, {"time_grain": "quarter"}).sql
    with pytest.raises(RenderError, match="no 'day' format"):
        render(definition, {"time_grain": "day"})


def test_snowflake_trunc_form_is_reported_by_the_round_trip():
    """Known sqlglot 30.31.0 behavior, independent of grain: Snowflake TRUNC(ts, 'MONTH')
    is written back as DATE_TRUNC('MONTH', ts), which parses as another node, so the
    same-query-back check reports a difference instead of passing silently."""
    sql = "SELECT TRUNC(o.created_at, 'MONTH') AS m FROM orders AS o"
    definition = build(sql, Dialect.SNOWFLAKE, [grain("main.G1")])
    assert not roundtrip_check(sql, definition).identical


# ── Several places, nesting, values inside truncations ──────────────────────────────

TWO_CTES = """
WITH booked AS (
  SELECT DATE_TRUNC('month', created_at) AS m, COUNT(*) AS n FROM orders GROUP BY 1
), shipped AS (
  SELECT DATE_TRUNC('month', created_at) AS m, COUNT(*) AS n FROM orders WHERE id > 0 GROUP BY 1
)
SELECT b.m, b.n, s.n AS shipped FROM booked AS b JOIN shipped AS s ON s.m = b.m
"""


def test_one_grain_in_several_places():
    definition = build(TWO_CTES, Dialect.DUCKDB, [grain("booked.G1", "shipped.G1")])
    spec = definition.variable("time_grain")
    assert [s.placeholder for s in spec.slots] == ["time_grain", "time_grain__2"]
    assert roundtrip_check(TWO_CTES, definition).identical
    tree, _, _ = render_tree(definition, {"time_grain": "year"})
    assert [g.unit for g in find_grains(tree, Dialect.DUCKDB)] == ["year", "year"]


def test_places_with_different_units_are_rejected():
    sql = TWO_CTES.replace(
        "DATE_TRUNC('month', created_at) AS m, COUNT(*) AS n FROM orders W",
        "DATE_TRUNC('week', created_at) AS m, COUNT(*) AS n FROM orders W",
    )
    with pytest.raises(AnnotationError, match="same original grain.*booked.G1 = month.*week"):
        build(sql, Dialect.DUCKDB, [grain("booked.G1", "shipped.G1")])


def test_nested_truncations_are_separate_variables():
    sql = "SELECT DATE_TRUNC('month', DATE_TRUNC('day', created_at)) AS m FROM orders"
    definition = build(
        sql, Dialect.DUCKDB, [grain("main.G1"), grain("main.G2", name="inner_grain")]
    )
    assert roundtrip_check(sql, definition).identical
    result = render(definition, {"time_grain": "year", "inner_grain": "week"})
    assert "DATE_TRUNC('YEAR', DATE_TRUNC('WEEK', created_at))" in result.sql


def test_values_inside_a_rebuilt_truncation_are_still_filled():
    sql = """
    SELECT id FROM orders
    WHERE DATE_TRUNC('month', created_at) = DATE_TRUNC('month', TIMESTAMP '2026-03-05')
    """
    annotations = [
        grain("main.G1", "main.G2", role=Role.CHANGEABLE),
        Annotation(
            name="as_of", type=VariableType.DATE, role=Role.CHANGEABLE, literals=["main.L1"]
        ),
    ]
    definition = build(sql, Dialect.DUCKDB, annotations)
    assert "$as_of" in definition.template
    assert roundtrip_check(sql, definition).identical
    result = render(definition, {"time_grain": "quarter", "as_of": "2026-08-20"})
    assert (
        "DATE_TRUNC('QUARTER', created_at) = "
        "DATE_TRUNC('QUARTER', CAST('2026-08-20' AS TIMESTAMP))" in result.sql
    )


# ── Annotation errors and issues ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("annotation", "message"),
    [
        (grain("main.G7"), r"unknown grain ID main.G7.*Known: main.G1"),
        (grain("main.L1"), r"unknown grain ID main.L1"),
        (
            Annotation(name="g", type=VariableType.TEXT, literals=["main.G1"]),
            r"unknown literal main.G1",
        ),
    ],
)
def test_wrong_ids_are_rejected(annotation, message):
    with pytest.raises(AnnotationError, match=message):
        build(SOURCES[Dialect.DUCKDB], Dialect.DUCKDB, [annotation])


def test_a_grain_id_used_twice_is_rejected():
    with pytest.raises(AnnotationError, match="main.G1 is used by two variables"):
        build(
            SOURCES[Dialect.DUCKDB],
            Dialect.DUCKDB,
            [grain("main.G1"), grain("main.G1", name="other")],
        )


@pytest.mark.parametrize(
    ("dialect", "sql", "unit"),
    [
        (Dialect.DUCKDB, "SELECT DATE_TRUNC('hour', created_at) FROM orders", "hour"),
        (Dialect.BIGQUERY, "SELECT DATE_TRUNC(day, WEEK(MONDAY)) FROM orders", "week(monday)"),
        (Dialect.BIGQUERY, "SELECT DATE_TRUNC(day, ISOWEEK) FROM orders", "isoweek"),
    ],
)
def test_units_outside_the_five_grains_cannot_be_a_grain_variable(dialect, sql, unit):
    with pytest.raises(AnnotationError, match=rf"truncates to '{re.escape(unit)}'"):
        build(sql, dialect, [grain("main.G1")])


def test_format_and_unit_options_are_rejected_for_grain():
    with pytest.raises(AnnotationError, match="format applies to date"):
        build(SOURCES[Dialect.DUCKDB], Dialect.DUCKDB, [grain("main.G1", format="%Y")])
    with pytest.raises(AnnotationError, match="unit applies to number"):
        build(SOURCES[Dialect.DUCKDB], Dialect.DUCKDB, [grain("main.G1", unit="days")])


def test_changeable_grain_in_an_or_branch_needs_a_description():
    sql = "SELECT id FROM orders WHERE DATE_TRUNC('month', created_at) = created_at OR id = 1"
    analysis = analyze(sql, Dialect.DUCKDB, SCHEMA)
    issues = apply_annotations(sql, Dialect.DUCKDB, analysis, [grain("main.G1")]).issues
    assert [(i.code, i.severity, i.variable) for i in issues] == [
        (IssueCode.DESCRIPTION_REQUIRED, Severity.ERROR, "time_grain")
    ]
    described = grain("main.G1", description="Compare at this grain.")
    assert apply_annotations(sql, Dialect.DUCKDB, analysis, [described]).issues == []


# ── Render values and issues ────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw", ["Q", "QUARTER", "monthly", 3, None])
def test_invalid_grain_input_is_rejected_at_render(raw):
    definition = build(SOURCES[Dialect.DUCKDB], Dialect.DUCKDB, [grain("main.G1")])
    with pytest.raises(VariableValueError, match=r"Expected one of \"day\" \| \"week\""):
        render(definition, {"time_grain": raw})


def test_fixed_grain_override_and_allowed_values_warn():
    definition = build(
        SOURCES[Dialect.DUCKDB],
        Dialect.DUCKDB,
        [grain("main.G1", role=Role.FIXED, allowed=["month", "quarter"])],
    )
    assert render(definition, {"time_grain": "month"}).issues == []
    issues = render(definition, {"time_grain": "week"}).issues
    assert [i.code for i in issues] == [IssueCode.FIXED_OVERRIDE, IssueCode.VALUE_NOT_ALLOWED]


def test_tampered_or_missing_truncation_fails_render():
    definition = build(SOURCES[Dialect.DUCKDB], Dialect.DUCKDB, [grain("main.G1")])
    drifted = definition.model_copy(
        update={"template": definition.template.replace("'MONTH'", "'YEAR'")}
    )
    with pytest.raises(RenderError, match="main.G1 is 'year', expected 'month'"):
        render(drifted, {})
    gone = definition.model_copy(update={"template": "SELECT o.created_at AS m FROM orders AS o"})
    with pytest.raises(RenderError, match="grain main.G1 is not in the template"):
        render(gone, {"time_grain": "week"})


def test_transpiled_week_follows_sqlglot_and_is_labeled():
    """Week start is not translated between warehouses except where sqlglot does it
    (BigQuery → DuckDB); transpiled renders are labeled (ADR 0013, DESIGN §13)."""
    duck = build(SOURCES[Dialect.DUCKDB], Dialect.DUCKDB, [grain("main.G1")])
    to_bq = render(duck, {"time_grain": "week"}, Dialect.BIGQUERY)
    assert "TIMESTAMP_TRUNC(o.created_at, WEEK)" in to_bq.sql  # Sunday weeks in BigQuery.
    assert to_bq.transpiled and IssueCode.TRANSPILED in [i.code for i in to_bq.issues]
    bq = build(
        "SELECT DATE_TRUNC(o.day, MONTH) AS m FROM orders AS o",
        Dialect.BIGQUERY,
        [grain("main.G1")],
    )
    to_duck = render(bq, {"time_grain": "week"}, Dialect.DUCKDB)
    assert "DATE_TRUNC('WEEK', o.day + INTERVAL '1' DAY) + INTERVAL '-1' DAY" in to_duck.sql


# ── The example: realization_rate month → quarter ───────────────────────────────────


@pytest.fixture(scope="module")
def grain_example(sample_sql, sample_schema) -> Definition:
    text = (EXAMPLES / "definitions" / "realization_rate_grain.annotations.yml").read_text()
    annotations = annotations_from_yaml(text)
    return build(sample_sql, Dialect.DUCKDB, annotations.variables, sample_schema)


def test_example_grain_variable(grain_example, sample_sql):
    spec = grain_example.variable("time_grain")
    assert spec.original_value == "month"
    assert spec.column == "deals.closed_at" and spec.column_type == "TIMESTAMP"
    assert [s.literals for s in spec.slots] == [["deals_in_region.G1"]]
    assert roundtrip_check(sample_sql, grain_example).identical


def test_example_renders_quarterly(grain_example):
    result = render(grain_example, {"time_grain": "quarter"})
    assert "DATE_TRUNC('QUARTER', cd.closed_at) AS month" in result.sql
    assert "DATE_TRUNC('MONTH'" not in result.sql
    assert result.values["time_grain"] == "quarter"
    assert result.values["period"] == {"start": "2026-01-01", "end": "2026-04-01"}


# ── Units read from a column are not grains ─────────────────────────────────────────


def test_a_qualified_column_unit_cannot_be_a_grain_and_stays_in_the_template():
    sql = "SELECT DATE_TRUNC(cfg.month, o.created_at) AS m FROM orders AS o CROSS JOIN cfg"
    with pytest.raises(AnnotationError, match=r"truncates to 'column cfg.month'"):
        build(sql, Dialect.DUCKDB, [grain("main.G1")])
    # Without a grain variable the column stays a column.
    definition = build(sql, Dialect.DUCKDB, [])
    assert "DATE_TRUNC(cfg.month, o.created_at)" in render(definition, {}).sql


@pytest.mark.parametrize(
    ("dialect", "sql"),
    [
        (Dialect.DUCKDB, "SELECT DATE_TRUNC(month, o.created_at) AS m FROM orders AS o, cfg"),
        (Dialect.POSTGRES, "SELECT date_trunc(grain, o.created_at) AS m FROM orders AS o, cfg"),
        (Dialect.DATABRICKS, "SELECT TRUNC(o.day, fmt) AS m FROM orders AS o, cfg"),
    ],
)
def test_a_bare_column_unit_is_refused_because_sqlglot_would_make_it_a_constant(dialect, sql):
    with pytest.raises(AnnotationError, match=r"unit is the column .*Qualify the column"):
        build(sql, dialect, [])


# ── A grain must cover every truncation to its unit ─────────────────────────────────

GROUP_BY_COPY = (
    "SELECT DATE_TRUNC('month', o.created_at) AS m, COUNT(*) AS n FROM orders AS o "
    "GROUP BY DATE_TRUNC('month', o.created_at)"
)
JOINED_ON_PERIOD = """
WITH o AS (
  SELECT DATE_TRUNC('month', created_at) AS m, COUNT(*) AS n FROM orders GROUP BY 1
), r AS (
  SELECT DATE_TRUNC('month', created_at) AS m, COUNT(*) AS n FROM refunds GROUP BY 1
)
SELECT o.m, o.n, r.n AS refunds FROM o JOIN r ON r.m = o.m
"""
UNION_BRANCHES = (
    "SELECT DATE_TRUNC('month', created_at) AS m FROM orders "
    "UNION ALL SELECT DATE_TRUNC('month', created_at) AS m FROM refunds"
)


@pytest.mark.parametrize(
    ("sql", "annotated", "left"),
    [
        (GROUP_BY_COPY, "main.G1", ["main.G2"]),
        (JOINED_ON_PERIOD, "o.G1", ["r.G1"]),
        (UNION_BRANCHES, "main.G1", ["main.G2"]),
    ],
)
def test_a_grain_covering_only_some_same_unit_truncations_is_an_error(sql, annotated, left):
    analysis = analyze(sql, Dialect.DUCKDB, SCHEMA)
    issues = apply_annotations(sql, Dialect.DUCKDB, analysis, [grain(annotated)]).issues
    assert [(i.code, i.severity, i.variable, i.literal) for i in issues] == [
        (IssueCode.GRAIN_PARTIAL, Severity.ERROR, "time_grain", left[0])
    ]
    assert all(grain_id in issues[0].message for grain_id in left)
    # Covering every month truncation clears it, and every one follows the value.
    everything = [g.id for g in analysis.grains]
    annotated_all = apply_annotations(sql, Dialect.DUCKDB, analysis, [grain(*everything)])
    assert annotated_all.issues == []
    definition = build(sql, Dialect.DUCKDB, [grain(*everything)])
    tree, _, render_issues = render_tree(definition, {"time_grain": "quarter"})
    assert render_issues == []
    assert {g.unit for g in find_grains(tree, Dialect.DUCKDB)} == {"quarter"}


def test_other_units_and_other_grain_variables_count_as_covered():
    sql = (
        "SELECT DATE_TRUNC('month', created_at) AS m, DATE_TRUNC('year', created_at) AS y, "
        "DATE_TRUNC('month', created_at) AS m2 FROM orders"
    )
    analysis = analyze(sql, Dialect.DUCKDB, SCHEMA)
    issues = apply_annotations(
        sql, Dialect.DUCKDB, analysis, [grain("main.G1"), grain("main.G3", name="second")]
    ).issues
    assert issues == []


def test_render_warns_when_a_stored_definition_changes_only_part_of_a_grain():
    """Definitions stored before the import check still render, with a warning."""
    definition = build(GROUP_BY_COPY, Dialect.DUCKDB, [grain("main.G1")])
    assert render(definition, {"time_grain": "month"}).issues == []
    issues = render(definition, {"time_grain": "quarter"}).issues
    assert [(i.code, i.severity, i.literal) for i in issues] == [
        (IssueCode.GRAIN_PARTIAL, Severity.WARNING, "main.G2")
    ]
