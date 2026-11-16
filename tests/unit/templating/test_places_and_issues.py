"""Multi-place variables, range shapes, structural facts → issues, expression types (ADR 0015)."""

import pytest

from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import AnnotationError
from cteforge.domain.issues import IssueCode, Severity
from cteforge.domain.models import Annotation, Definition, LiteralForm
from cteforge.domain.variables import RangeShape, Role, VariableType
from cteforge.parsing.analyze import analyze
from cteforge.templating.render import render
from cteforge.templating.roundtrip import roundtrip_check
from cteforge.templating.template import apply_annotations

D = Dialect.DUCKDB
SCHEMA = {
    "t": {"d": "DATE", "s": "VARCHAR", "ts": "TIMESTAMP", "n": "INTEGER", "other": "VARCHAR"},
    "u": {"d": "DATE", "s": "VARCHAR", "n": "INTEGER"},
}
RANGE = VariableType.DATE_RANGE


def _apply(sql: str, *annotations: Annotation, schema=SCHEMA):
    annotated = apply_annotations(sql, D, analyze(sql, D, schema), list(annotations))
    definition = Definition(
        id="x",
        title="x",
        source_dialect=D,
        template=annotated.template,
        variables=annotated.variables,
    )
    return definition, annotated.issues


def _codes(issues):
    return [(i.code, i.severity) for i in issues]


# ── multi-place variables ───────────────────────────────────────────────────────────


def test_example_period_binds_two_places_with_their_own_forms(
    collections_sql, collections_definition
):
    period = collections_definition.variable("period")
    assert [s.placeholder for s in period.slots] == [
        "period__start",
        "period__end",
        "period__2__start",
        "period__2__end",
    ]
    assert [(s.literal_form, s.format) for s in period.slots] == [
        (LiteralForm.BARE, "%m-%d-%Y"),
        (LiteralForm.BARE, "%m-%d-%Y"),
        (LiteralForm.TYPED, None),
        (LiteralForm.TYPED, None),
    ]
    assert period.shape is RangeShape.HALF_OPEN
    assert period.original_value == {"start": "2025-07-01", "end": "2025-10-01"}
    assert roundtrip_check(collections_sql, collections_definition).identical
    sql = render(
        collections_definition, {"period": {"start": "2026-01-15", "end": "2026-03-01"}}
    ).sql
    assert "STRPTIME('01-15-2026', '%m-%d-%Y')" in sql
    assert "i.invoiced_at < CAST('2026-03-01' AS TIMESTAMP)" in sql


def test_places_must_hold_the_same_value():
    sql = (
        "SELECT * FROM t WHERE d >= '2026-01-01' AND d < '2026-04-01'"
        " AND ts >= '2026-01-01' AND ts < '2026-05-01'"
    )
    ann = Annotation(name="p", type=RANGE, literals=["main.L1", "main.L2", "main.L3", "main.L4"])
    with pytest.raises(AnnotationError, match="same original value"):
        _apply(sql, ann)


def test_date_range_needs_pairs():
    sql = "SELECT * FROM t WHERE d >= '2026-01-01' AND d < '2026-04-01' AND ts >= '2026-01-01'"
    ann = Annotation(name="p", type=RANGE, literals=["main.L1", "main.L2", "main.L3"])
    with pytest.raises(AnnotationError, match="pairs"):
        _apply(sql, ann)


def test_scalar_places_mix_typed_and_bare_and_numbers_keep_their_text():
    sql = (
        "SELECT * FROM t JOIN u ON u.n = t.n WHERE t.d >= DATE '2026-01-01' AND u.d >= '2026-01-01'"
        " AND t.n > 1000.50 AND u.n > 1000.5"
    )
    definition, issues = _apply(
        sql,
        Annotation(name="since", type=VariableType.DATE, literals=["main.L1", "main.L2"]),
        Annotation(name="floor", type=VariableType.NUMBER, literals=["main.L3", "main.L4"]),
    )
    assert issues == []
    assert roundtrip_check(sql, definition).identical
    since = definition.variable("since")
    assert [s.literal_form for s in since.slots] == [LiteralForm.TYPED, LiteralForm.BARE]
    assert [s.placeholder for s in since.slots] == ["since", "since__2"]
    assert [s.original_text for s in definition.variable("floor").slots] == ["1000.50", "1000.5"]
    out = render(definition, {"since": "2026-06-01", "floor": 7}).sql
    assert "t.d >= CAST('2026-06-01' AS DATE)" in out and "u.d >= '2026-06-01'" in out
    assert "t.n > 7" in out and "u.n > 7" in out


def test_text_list_places_are_separated_by_in_group():
    sql = "SELECT * FROM t JOIN u ON u.n = t.n WHERE t.s IN ('a', 'b') AND u.s IN ('a', 'b')"
    ann = Annotation(
        name="codes",
        type=VariableType.TEXT_LIST,
        literals=["main.L1", "main.L2", "main.L3", "main.L4"],
    )
    definition, _ = _apply(sql, ann)
    assert [s.literals for s in definition.variables[0].slots] == [
        ["main.L1", "main.L2"],
        ["main.L3", "main.L4"],
    ]
    assert roundtrip_check(sql, definition).identical
    out = render(definition, {"codes": ["z"]}).sql
    assert "t.s IN ('z')" in out and "u.s IN ('z')" in out


def test_text_list_places_must_match():
    sql = "SELECT * FROM t JOIN u ON u.n = t.n WHERE t.s IN ('a', 'b') AND u.s IN ('a', 'c')"
    ann = Annotation(
        name="codes",
        type=VariableType.TEXT_LIST,
        literals=["main.L1", "main.L2", "main.L3", "main.L4"],
    )
    with pytest.raises(AnnotationError, match="same original value"):
        _apply(sql, ann)


def test_text_list_rejects_wrapped_items_and_non_literal_items():
    wrapped = "SELECT * FROM t WHERE s IN (UPPER('a'), 'b')"
    ann = Annotation(name="c", type=VariableType.TEXT_LIST, literals=["main.L1", "main.L2"])
    with pytest.raises(AnnotationError, match="inside a wrapper"):
        _apply(wrapped, ann)
    mixed = "SELECT * FROM t WHERE s IN ('a', t.other)"
    ann = Annotation(name="c", type=VariableType.TEXT_LIST, literals=["main.L1"])
    with pytest.raises(AnnotationError, match="non-literal items"):
        _apply(mixed, ann)


# ── range shapes and descriptions ───────────────────────────────────────────────────


OR_SQL = "SELECT * FROM t WHERE (d >= '2026-01-01' OR s = 'x') AND d < '2026-04-01'"


def test_non_candidate_pair_has_no_shape_and_needs_a_description():
    ann = Annotation(name="p", type=RANGE, role=Role.CHANGEABLE, literals=["main.L1", "main.L3"])
    definition, issues = _apply(OR_SQL, ann)
    assert definition.variables[0].shape is None
    assert _codes(issues) == [
        (IssueCode.RANGE_NOT_SIMPLE, Severity.WARNING),
        (IssueCode.DESCRIPTION_REQUIRED, Severity.ERROR),
    ]
    assert "or_branch" in issues[1].message and "not a simple range" in issues[1].message
    assert roundtrip_check(OR_SQL, definition).identical


def test_description_satisfies_the_requirement():
    ann = Annotation(
        name="p",
        type=RANGE,
        role=Role.CHANGEABLE,
        literals=["main.L1", "main.L3"],
        description="Start applies only to rows that are not 'x'.",
    )
    _, issues = _apply(OR_SQL, ann)
    assert _codes(issues) == [(IssueCode.RANGE_NOT_SIMPLE, Severity.WARNING)]


def test_negated_range_keeps_shape_but_needs_a_description_when_changeable():
    sql = "SELECT * FROM t WHERE NOT (d >= '2026-01-01' AND d < '2026-04-01')"
    ann = Annotation(
        name="excluded", type=RANGE, role=Role.CHANGEABLE, literals=["main.L1", "main.L2"]
    )
    definition, issues = _apply(sql, ann)
    assert definition.variables[0].shape is RangeShape.HALF_OPEN
    assert _codes(issues) == [(IssueCode.DESCRIPTION_REQUIRED, Severity.ERROR)]
    fixed = ann.model_copy(update={"role": Role.FIXED})
    assert _apply(sql, fixed)[1] == []


@pytest.mark.parametrize("op", ["<>", "!="])
def test_changeable_value_compared_with_not_equal_needs_a_description(op: str):
    """`s <> 'void'` EXCLUDES the value: a later agent passing 'paid' would silently drop
    paid rows, so the analyst must say so."""
    sql = f"SELECT * FROM t WHERE s {op} 'void'"
    ann = Annotation(name="st", type=VariableType.TEXT, role=Role.CHANGEABLE, literals=["main.L1"])
    _, issues = _apply(sql, ann)
    assert _codes(issues) == [(IssueCode.DESCRIPTION_REQUIRED, Severity.ERROR)]
    assert "negated" in issues[0].message
    described = ann.model_copy(update={"description": "Status to EXCLUDE."})
    assert _apply(sql, described)[1] == []


# ── types (compared expression type) ────────────────────────────────────────────────


def test_type_checked_against_the_compared_expression():
    sql = "SELECT * FROM t WHERE EXTRACT(YEAR FROM ts) = 2026 AND CAST(ts AS DATE) >= '2026-01-01'"
    year = Annotation(name="year", type=VariableType.NUMBER, literals=["main.L1"])
    since = Annotation(name="since", type=VariableType.DATE, literals=["main.L2"])
    definition, issues = _apply(sql, year, since)
    assert issues == []
    assert definition.variable("year").column_type == "BIGINT"
    assert definition.variable("since").column_type == "DATE"


def test_type_mismatch_is_an_error_issue():
    sql = "SELECT * FROM t WHERE EXTRACT(YEAR FROM ts) = 2026"
    wrong = Annotation(name="year", type=VariableType.BOOLEAN, literals=["main.L1"])
    with pytest.raises(AnnotationError):  # 2026 isn't TRUE/FALSE: no template at all
        _apply(sql, wrong)
    sql = "SELECT * FROM t WHERE s = '2026-01-01'"
    _, issues = _apply(sql, Annotation(name="d", type=VariableType.DATE, literals=["main.L1"]))
    assert _codes(issues) == [(IssueCode.TYPE_MISMATCH, Severity.ERROR)]


def test_unknown_type_is_a_warning_not_silence():
    sql = "SELECT * FROM t WHERE s = 'a'"
    _, issues = _apply(
        sql, Annotation(name="s", type=VariableType.TEXT, literals=["main.L1"]), schema={}
    )
    assert [(i.code, i.severity, i.literal) for i in issues] == [
        (IssueCode.TYPE_UNKNOWN, Severity.WARNING, "main.L1")
    ]
