"""Numbers keep exact text; date formats and units (ADR 0014)."""

import pytest

from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import AnnotationError, VariableValueError
from cteforge.domain.issues import IssueCode, Severity
from cteforge.domain.models import Annotation, Definition
from cteforge.domain.variables import VariableType
from cteforge.parsing.analyze import analyze
from cteforge.templating.render import render
from cteforge.templating.roundtrip import roundtrip_check
from cteforge.templating.template import apply_annotations

D = Dialect.DUCKDB
SCHEMA = {"t": {"amount": "DECIMAL(12,2)", "d": "DATE", "s": "VARCHAR", "ts": "TIMESTAMP"}}


def _define(sql: str, *annotations: Annotation, schema=SCHEMA):
    annotated = apply_annotations(sql, D, analyze(sql, D, schema), list(annotations))
    definition = Definition(
        id="x",
        title="x",
        source_dialect=D,
        template=annotated.template,
        variables=annotated.variables,
    )
    return definition, annotated.issues


def _number(sql: str, **kw) -> Definition:
    ann = Annotation(name="n", type=VariableType.NUMBER, literals=["main.L1"], **kw)
    return _define(sql, ann)[0]


@pytest.mark.parametrize("text", ["1000.50", "1e3", "0.10", "-5", "1.5E-3"])
def test_original_number_text_round_trips(text):
    sql = f"SELECT * FROM t WHERE amount > {text}"
    definition = _number(sql)
    assert roundtrip_check(sql, definition).identical
    assert definition.variables[0].slots[0].original_text == text
    assert f"amount > {text}" in render(definition, {}).sql


def test_original_value_is_plain_number():
    assert _number("SELECT * FROM t WHERE amount > 1e3").variables[0].original_value == 1000
    assert _number("SELECT * FROM t WHERE amount > 1000.50").variables[0].original_value == 1000.5


def test_same_value_keeps_original_text_new_value_uses_plain_decimal():
    definition = _number("SELECT * FROM t WHERE amount > 1000.50")
    assert "amount > 1000.50" in render(definition, {"n": 1000.5}).sql
    assert "amount > 0.0000001" in render(definition, {"n": 1e-7}).sql
    assert "amount > 100000000000000000000" in render(definition, {"n": 1e20}).sql
    assert "amount > -2.5" in render(definition, {"n": -2.5}).sql


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan"), "1000.50", True])
def test_render_rejects_non_numbers(bad):
    definition = _number("SELECT * FROM t WHERE amount > 10")
    with pytest.raises(VariableValueError, match="JSON number"):
        render(definition, {"n": bad})


def test_rendered_numbers_are_numeric_literals_not_identifiers():
    from sqlglot import exp, parse_one

    definition = _number("SELECT * FROM t WHERE amount > 10")
    for value in (1e300, -1e-300, 123456789, 0):
        tree = parse_one(render(definition, {"n": value}).sql, read="duckdb")
        compared = tree.find(exp.GT).expression
        literal = compared.this if isinstance(compared, exp.Neg) else compared
        assert isinstance(literal, exp.Literal) and not literal.is_string


def test_number_with_more_precision_than_json_is_rejected():
    sql = "SELECT * FROM t WHERE amount > 0.12345678901234567890123"
    with pytest.raises(AnnotationError, match="more precision"):
        _number(sql)


def test_unit_is_carried_and_only_for_numbers():
    sql = "SELECT * FROM t WHERE amount > 500 * 1e12 AND s = 'x'"
    definition = _number(sql, unit="TFLOPS")
    assert definition.variables[0].unit == "TFLOPS"
    assert "amount > 750 * 1e12" in render(definition, {"n": 750}).sql
    with pytest.raises(AnnotationError, match="unit applies to number only"):
        _define(sql, Annotation(name="s", type=VariableType.TEXT, literals=["main.L3"], unit="x"))


# ── format ──────────────────────────────────────────────────────────────────────────


def test_suggested_format_from_strptime_is_used():
    sql = "SELECT * FROM t WHERE ts >= strptime('03-04-2026', '%m-%d-%Y')"
    definition, issues = _define(
        sql, Annotation(name="since", type=VariableType.DATE, literals=["main.L1"])
    )
    spec = definition.variables[0]
    assert spec.original_value == "2026-03-04"
    assert spec.format is None and spec.slots[0].format == "%m-%d-%Y"
    assert issues == []  # day/month ambiguous, but the SQL itself states the format
    assert roundtrip_check(sql, definition).identical
    assert "STRPTIME('12-25-2026', '%m-%d-%Y')" in render(definition, {"since": "2026-12-25"}).sql


def test_explicit_format_reads_bare_text_and_warns_when_ambiguous():
    sql = "SELECT * FROM t WHERE s = '03/04/2026'"
    ann = Annotation(name="day", type=VariableType.DATE, literals=["main.L1"], format="%m/%d/%Y")
    definition, issues = _define(sql, ann, schema={})
    assert definition.variables[0].original_value == "2026-03-04"
    assert definition.variables[0].format == "%m/%d/%Y"
    codes = [(i.code, i.severity, i.literal) for i in issues]
    assert (IssueCode.AMBIGUOUS_DATE_FORMAT, Severity.WARNING, "main.L1") in codes
    assert roundtrip_check(sql, definition).identical
    assert "s = '12/25/2026'" in render(definition, {"day": "2026-12-25"}).sql


def test_explicit_format_without_ambiguity_does_not_warn():
    sql = "SELECT * FROM t WHERE s = '01/25/2026'"
    ann = Annotation(name="day", type=VariableType.DATE, literals=["main.L1"], format="%m/%d/%Y")
    _, issues = _define(sql, ann, schema={})
    assert IssueCode.AMBIGUOUS_DATE_FORMAT not in [i.code for i in issues]


def test_non_iso_text_without_format_is_rejected():
    sql = "SELECT * FROM t WHERE s = '01/25/2026'"
    ann = Annotation(name="day", type=VariableType.DATE, literals=["main.L1"])
    with pytest.raises(AnnotationError, match="set `format`"):
        _define(sql, ann)


def test_wrong_format_is_an_annotation_error():
    sql = "SELECT * FROM t WHERE s = '25/01/2026'"
    ann = Annotation(name="day", type=VariableType.DATE, literals=["main.L1"], format="%m/%d/%Y")
    with pytest.raises(AnnotationError, match="can't be read with format '%m/%d/%Y'"):
        _define(sql, ann)


def test_format_that_does_not_write_back_the_same_text_is_rejected():
    sql = "SELECT * FROM t WHERE s = '1/5/2026'"
    ann = Annotation(name="day", type=VariableType.DATE, literals=["main.L1"], format="%m/%d/%Y")
    with pytest.raises(AnnotationError, match="writes it back as '01/05/2026'"):
        _define(sql, ann)


@pytest.mark.parametrize("vtype", [VariableType.TEXT, VariableType.NUMBER])
def test_format_only_for_dates(vtype):
    sql = "SELECT * FROM t WHERE s = 'a' AND amount > 1"
    literal = "main.L1" if vtype is VariableType.TEXT else "main.L2"
    ann = Annotation(name="v", type=vtype, literals=[literal], format="%Y")
    with pytest.raises(AnnotationError, match="format applies to date and date_range only"):
        _define(sql, ann)


def test_typed_literal_stays_iso_even_with_a_format():
    sql = "SELECT * FROM t WHERE d >= DATE '2026-01-05' AND s = '01/05/2026'"
    ann = Annotation(
        name="day", type=VariableType.DATE, literals=["main.L1", "main.L2"], format="%m/%d/%Y"
    )
    definition, _ = _define(sql, ann)
    assert [s.format for s in definition.variables[0].slots] == [None, "%m/%d/%Y"]
    assert roundtrip_check(sql, definition).identical
    sql_out = render(definition, {"day": "2026-02-03"}).sql
    assert "CAST('2026-02-03' AS DATE)" in sql_out and "s = '02/03/2026'" in sql_out
