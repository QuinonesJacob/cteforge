"""MySQL templates and rendering (ADR 0028): `:name` placeholders round-trip, literals stay
one literal with the exact value under MySQL's default sql_mode (checked with an
independent reader of MySQL string literals, not sqlglot), grains rebuilt in the
DATE_FORMAT family, and the shop definition in tests/fixtures/mysql (also used by the live
tests).

Golden files (tests/golden/paid_revenue*.mysql.sql) update only deliberately:
    UPDATE_GOLDEN=1 uv run pytest tests/unit/templating/test_mysql_templates.py
"""

import json
import os
from pathlib import Path

import pytest
from sqlglot import exp

from cteforge.domain.codec import annotations_from_yaml
from cteforge.domain.dialects import Dialect
from cteforge.domain.issues import Severity
from cteforge.domain.models import Annotation, Definition
from cteforge.domain.variables import Role, VariableType
from cteforge.parsing.analyze import analyze, find_grains, generate, parse
from cteforge.templating.render import render, render_tree, template_slots
from cteforge.templating.roundtrip import roundtrip_check
from cteforge.templating.template import apply_annotations

MY = Dialect.MYSQL
TESTS = Path(__file__).resolve().parents[2]
FIXTURES = TESTS / "fixtures" / "mysql"
GOLDEN = TESTS / "golden"

# What information_schema.columns.column_type reports (tests/fixtures/mysql/live_setup.py).
SHOP_SCHEMA = {
    "shop.orders": {
        "order_id": "bigint unsigned",
        "customer_id": "bigint unsigned",
        "created_at": "datetime",
        "channel": "enum('web','app','store')",
        "status": "enum('new','paid','shipped','refunded')",
        "is_test": "tinyint(1)",
        "amount": "decimal(10,2)",
        "note": "varchar(200)",
    },
    "shop.customers": {"customer_id": "bigint unsigned", "country": "char(2)"},
}


def build(sql: str, annotations: list[Annotation], schema=None) -> Definition:
    annotated = apply_annotations(sql, MY, analyze(sql, MY, schema), annotations)
    assert [i for i in annotated.issues if i.severity is Severity.ERROR] == []
    return Definition(
        id="x",
        title="x",
        source_dialect=MY,
        template=annotated.template,
        variables=annotated.variables,
    )


def shop_definition() -> Definition:
    sql = (FIXTURES / "paid_revenue.sql").read_text()
    meta = annotations_from_yaml((FIXTURES / "paid_revenue.annotations.yml").read_text())
    annotated = apply_annotations(sql, MY, analyze(sql, MY, SHOP_SCHEMA), meta.variables)
    assert [i for i in annotated.issues if i.severity is Severity.ERROR] == []
    return Definition(
        id=meta.id,
        title=meta.title,
        source_dialect=MY,
        template=annotated.template,
        variables=annotated.variables,
    )


def grain(*ids: str) -> Annotation:
    return Annotation(
        name="time_grain", type=VariableType.GRAIN, role=Role.CHANGEABLE, literals=list(ids)
    )


# ── An independent reader of MySQL string literals (default sql_mode) ───────────────
# MySQL 8.4 reference, "String Literals": in '...' and "..." a backslash starts an escape
# (\0 \' \" \b \n \r \t \Z \\ map to characters; \% and \_ keep the backslash; any other
# \x is x), a doubled quote is one quote. Comments: '-- ' (dash dash space), '#', /* */.

_ESCAPES = {"0": "\0", "'": "'", '"': '"', "b": "\b", "n": "\n", "r": "\r", "t": "\t"}
_ESCAPES |= {"Z": "\x1a", "\\": "\\", "%": "\\%", "_": "\\_"}


def mysql_strings(sql: str) -> list[str]:
    """Every string literal's value in `sql`, read as MySQL reads it."""
    values: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if sql.startswith("-- ", i) or ch == "#":
            end = sql.find("\n", i)
            i = n if end < 0 else end + 1
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            assert end >= 0, "unclosed comment"
            i = end + 2
        elif ch == "`":
            end = sql.find("`", i + 1)
            assert end >= 0, "unclosed identifier"
            i = end + 1
        elif ch in "'\"":
            out, j = [], i + 1
            while True:
                assert j < n, "unclosed string"
                c = sql[j]
                if c == "\\":
                    out.append(_ESCAPES.get(sql[j + 1], sql[j + 1]))
                    j += 2
                elif c == ch and sql[j + 1 : j + 2] == ch:
                    out.append(ch)
                    j += 2
                elif c == ch:
                    break
                else:
                    out.append(c)
                    j += 1
            values.append("".join(out))
            i = j + 1
        else:
            i += 1
    return values


# Text values that must stay one literal with the exact value (also stored in the live
# server's shop.strings by tests/fixtures/mysql/live_setup.py).
HOSTILE = json.loads((FIXTURES / "hostile_strings.json").read_text(encoding="ascii"))


def text_definition() -> Definition:
    sql = (
        "SELECT o.order_id FROM shop.orders AS o "
        "WHERE o.note = 'x' AND o.note LIKE 'y%' AND o.status IN ('paid', 'shipped')"
    )
    return build(
        sql,
        [
            Annotation(name="note", type=VariableType.TEXT, literals=["main.L1"]),
            Annotation(name="pattern", type=VariableType.TEXT, literals=["main.L2"]),
            Annotation(
                name="statuses", type=VariableType.TEXT_LIST, literals=["main.L3", "main.L4"]
            ),
        ],
        SHOP_SCHEMA,
    )


def test_the_reference_reader_follows_the_mysql_manual():
    assert mysql_strings(r"SELECT 'a\'b', 'it''s', 'p\%q', '\x', '\0' -- 'no'") == [
        "a'b",
        "it's",
        "p\\%q",
        "x",
        "\0",
    ]


@pytest.mark.parametrize("value", HOSTILE)
def test_text_values_render_to_one_literal_with_the_exact_value(value):
    definition = text_definition()
    sql = render(definition, {"note": value, "pattern": value, "statuses": [value, "b"]}).sql
    # MySQL (default sql_mode) reads back exactly these four literals.
    assert mysql_strings(sql) == [value, value, value, "b"]
    # sqlglot agrees, and finds the same four filter literals: nothing broke out.
    literals = analyze(sql, MY).literals
    assert [lit.value for lit in literals] == [value, value, value, "b"]
    # Everything outside the literals is the template's SQL: with the value swapped for a
    # plain one, the tree equals the plain render's tree.
    tree = parse(sql, MY)
    for node in tree.find_all(exp.Literal):
        if node.is_string and node.this == value:
            node.set("this", "plain")
    plain = {"note": "plain", "pattern": "plain", "statuses": ["plain", "b"]}
    assert tree == parse(render(definition, plain).sql, MY)


def test_hostile_value_cannot_add_statements_or_comments():
    value = "x'; DELETE FROM t; /*! SLEEP(9) */ --"
    sql = render(text_definition(), {"note": value}).sql
    assert mysql_strings(sql) == [value, "y%", "paid", "shipped"]
    parse(sql, MY)  # one statement, no executable comment outside a literal


def test_placeholders_are_named_and_parse_back():
    definition = text_definition()
    template = definition.template
    assert "o.note = :note" in template and "IN (:statuses)" in template
    names = sorted(name for _, name in template_slots(parse(template, MY)))
    assert names == ["note", "pattern", "statuses"]


def test_native_literals_render_exactly():
    sql = (
        "SELECT o.order_id FROM shop.orders AS o "
        "WHERE o.created_at >= TIMESTAMP '2026-01-01' AND o.created_at < '2026-04-01' "
        "AND o.amount > 18446744073709551615 AND o.amount <= 1000.50 AND o.is_test = TRUE "
        "AND o.created_at >= STR_TO_DATE('01/15/2026', '%m/%d/%Y')"
    )
    definition = build(
        sql,
        [
            Annotation(name="since", type=VariableType.DATE, literals=["main.L1"]),
            Annotation(name="until", type=VariableType.DATE, literals=["main.L2"]),
            Annotation(name="big", type=VariableType.NUMBER, literals=["main.L3"]),
            Annotation(name="max_amount", type=VariableType.NUMBER, literals=["main.L4"]),
            Annotation(name="test", type=VariableType.BOOLEAN, literals=["main.L5"]),
            Annotation(name="us_date", type=VariableType.DATE, literals=["main.L6"]),
        ],
        SHOP_SCHEMA,
    )
    assert roundtrip_check(sql, definition).identical
    unchanged = render(definition, {}).sql
    assert "18446744073709551615" in unchanged and "1000.50" in unchanged
    out = render(
        definition,
        {
            "since": "2026-02-01",
            "until": "2026-03-01",
            "big": 5,
            "max_amount": -1.5,
            "test": False,
            "us_date": "2026-05-06",
        },
    ).sql
    # A TIMESTAMP literal is written back as a literal, not the TIMESTAMP() function.
    assert "o.created_at >= TIMESTAMP '2026-02-01'" in out
    assert "o.created_at < '2026-03-01'" in out
    assert "o.amount > 5" in out and "o.amount <= -1.5" in out and "o.is_test = FALSE" in out
    assert "STR_TO_DATE('05/06/2026', '%m/%d/%Y')" in out


def test_same_query_back_for_common_mysql_shapes():
    sql = (
        "SELECT `o`.`channel`, IFNULL(SUM(o.amount), 0) AS revenue, "
        "DATE_SUB(DATE(o.created_at), INTERVAL WEEKDAY(o.created_at) DAY) AS monday, "
        "GROUP_CONCAT(DISTINCT o.status ORDER BY o.status SEPARATOR ',') AS statuses "
        "FROM shop.orders AS o WHERE o.created_at >= TIMESTAMP '2026-01-01 00:00:00' "
        "AND o.created_at >= CURDATE() - INTERVAL 30 DAY AND o.note REGEXP '^a' "
        'AND o.note <=> NULL AND o.status = "paid" '
        "GROUP BY o.channel, monday ORDER BY revenue DESC LIMIT 5, 10"
    )
    definition = build(sql, [], SHOP_SCHEMA)
    report = roundtrip_check(sql, definition)
    assert report.identical, report.diff


# ── Grain (DATE_FORMAT family) ─────────────────────────────────────────────────────


def test_shop_definition_same_query_back_and_grains():
    definition = shop_definition()
    sql = (FIXTURES / "paid_revenue.sql").read_text()
    assert roundtrip_check(sql, definition).identical
    spec = definition.variable("time_grain")
    assert spec.original_value == "month"
    assert [s.literals for s in spec.slots] == [["main.G1"], ["main.G2"]]


@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        ("day", "DATE_FORMAT(o.created_at, '%Y-%m-%d')"),
        ("week", "DATE_FORMAT(o.created_at - INTERVAL (WEEKDAY(o.created_at)) DAY, '%Y-%m-%d')"),
        ("month", "DATE_FORMAT(o.created_at, '%Y-%m-01')"),
        (
            "quarter",
            "DATE_FORMAT(MAKEDATE(YEAR(o.created_at), 1) + INTERVAL (QUARTER(o.created_at) - 1) "
            "QUARTER, '%Y-%m-%d')",
        ),
        ("year", "DATE_FORMAT(o.created_at, '%Y-01-01')"),
    ],
)
def test_every_grain_renders_runnable_mysql_in_both_places(unit, expected):
    definition = shop_definition()
    result = render(definition, {"time_grain": unit})
    assert result.issues == [] and not result.transpiled
    flat = generate(parse(result.sql, MY), MY, pretty=False)
    assert flat.count(expected) == 2  # SELECT and GROUP BY change together
    tree = render_tree(definition, {"time_grain": unit})[0]
    reparsed = parse(result.sql, MY)
    assert reparsed == tree
    # The rendered SQL reads back with the requested grain (week / quarter shapes too).
    assert [g.unit for g in find_grains(reparsed, MY)] == [unit, unit]


def test_time_suffix_is_kept():
    sql = "SELECT DATE_FORMAT(o.created_at, '%Y-01-01 00:00:00') AS y FROM shop.orders AS o"
    definition = build(sql, [grain("main.G1")], SHOP_SCHEMA)
    assert definition.variable("time_grain").original_value == "year"
    out = render(definition, {"time_grain": "week"}).sql
    assert "'%Y-%m-%d 00:00:00'" in out
    assert [g.unit for g in find_grains(parse(out, MY), MY)] == ["week"]


@pytest.mark.parametrize(
    ("written", "unit"),
    [
        ("DATE_FORMAT(o.created_at - INTERVAL WEEKDAY(o.created_at) DAY, '%Y-%m-%d')", "week"),
        (
            "DATE_FORMAT(DATE_SUB(o.created_at, INTERVAL WEEKDAY(o.created_at) DAY), '%Y-%m-%d')",
            "week",
        ),
        (
            "DATE_FORMAT(MAKEDATE(YEAR(o.created_at), 1) + INTERVAL QUARTER(o.created_at) - 1 "
            "QUARTER, '%Y-%m-%d')",
            "quarter",
        ),
    ],
)
def test_week_and_quarter_shapes_are_original_grains(written, unit):
    sql = f"SELECT {written} AS p, COUNT(*) AS n FROM shop.orders AS o GROUP BY p"
    definition = build(sql, [grain("main.G1")], SHOP_SCHEMA)
    spec = definition.variable("time_grain")
    assert spec.original_value == unit and spec.column == "shop.orders.created_at"
    assert roundtrip_check(sql, definition).identical
    out = render(definition, {"time_grain": "month"}).sql
    assert "DATE_FORMAT(o.created_at, '%Y-%m-01')" in out


def test_computed_value_is_parenthesized_before_the_interval():
    sql = (
        "SELECT DATE_FORMAT(o.created_at + INTERVAL 7 HOUR, '%Y-%m-01') AS p FROM shop.orders AS o"
    )
    definition = build(sql, [grain("main.G1")], SHOP_SCHEMA)
    out = render(definition, {"time_grain": "week"}).sql
    tree = parse(out, MY)
    assert tree == render_tree(definition, {"time_grain": "week"})[0]
    flat = generate(tree, MY, pretty=False)
    assert "DATE_FORMAT((o.created_at + INTERVAL '7' HOUR) - INTERVAL" in flat
    assert [g.unit for g in find_grains(tree, MY)] == ["week"]


def test_nested_truncations_are_rebuilt_inside_out():
    sql = (
        "SELECT DATE_FORMAT(DATE_FORMAT(o.created_at, '%Y-%m-01'), '%Y-01-01') AS y "
        "FROM shop.orders AS o"
    )
    definition = build(
        sql,
        [
            grain("main.G1"),
            Annotation(
                name="inner", type=VariableType.GRAIN, role=Role.CHANGEABLE, literals=["main.G2"]
            ),
        ],
        SHOP_SCHEMA,
    )
    out = render(definition, {"time_grain": "week", "inner": "day"}).sql
    flat = generate(parse(out, MY), MY, pretty=False)
    inner = "DATE_FORMAT(o.created_at, '%Y-%m-%d')"
    # The outer week uses its value twice: both copies carry the rebuilt inner day.
    assert flat.count(inner) == 2 and "'%Y-%m-01'" not in flat


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT DATE(o.created_at) AS d FROM shop.orders AS o",
        "SELECT DATE_FORMAT(o.created_at, '%Y-%m') AS m FROM shop.orders AS o",
        "SELECT DATE_FORMAT(o.created_at, '%x-%v') AS w FROM shop.orders AS o",
        "SELECT DATE_FORMAT(o.created_at, '%Y-%m-%d %H:%i:%s') AS t FROM shop.orders AS o",
        "SELECT LAST_DAY(o.created_at) AS m FROM shop.orders AS o",
        "SELECT YEARWEEK(o.created_at, 3) AS w FROM shop.orders AS o",
        # Already aligned to another period (a Sunday week; a Monday of DATE(x)): another
        # grain would truncate that start, not the timestamp.
        "SELECT DATE_FORMAT(o.created_at - INTERVAL DAYOFWEEK(o.created_at) - 1 DAY, "
        "'%Y-%m-%d') AS w FROM shop.orders AS o",
        "SELECT DATE_FORMAT(DATE_SUB(DATE(o.created_at), INTERVAL WEEKDAY(o.created_at) DAY), "
        "'%Y-%m-01') AS m FROM shop.orders AS o",
        "SELECT DATE_FORMAT(MAKEDATE(YEAR(o.created_at), 1), '%Y-%m-%d') AS y "
        "FROM shop.orders AS o",
    ],
)
def test_casts_and_labels_are_not_grain_candidates(sql):
    assert analyze(sql, MY, SHOP_SCHEMA).grains == []


def test_date_format_is_a_grain_candidate_in_mysql_only():
    sql = "SELECT strftime(o.created_at, '%Y-%m-01') AS m FROM orders AS o"
    assert analyze(sql, Dialect.DUCKDB).grains == []


# ── Transpiling ──────────────────────────────────────────────────────────────────


def test_rendering_mysql_definitions_elsewhere_is_labeled_transpiled():
    definition = shop_definition()
    for target in (Dialect.DUCKDB, Dialect.POSTGRES):
        result = render(definition, {"time_grain": "month"}, target)
        assert result.transpiled


# ── Goldens ───────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "values"),
    [
        ("paid_revenue", {"period": {"start": "2026-02-01", "end": "2026-03-01"}}),
        (
            "paid_revenue_weekly",
            {
                "period": {"start": "2026-01-05", "end": "2026-02-02"},
                "statuses": ["paid"],
                "country": "FR",
                "min_amount": 25,
                "time_grain": "week",
            },
        ),
    ],
)
def test_golden(name, values):
    result = render(shop_definition(), values)
    path = GOLDEN / f"{name}.mysql.sql"
    if os.environ.get("UPDATE_GOLDEN") == "1":
        path.write_text(result.sql + "\n")
    assert path.exists(), f"Missing golden file {path.name}; create it with UPDATE_GOLDEN=1"
    assert result.sql + "\n" == path.read_text()
