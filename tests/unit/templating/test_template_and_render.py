import pytest

from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import AnnotationError, RenderError, VariableValueError
from cteforge.domain.models import Annotation, LiteralForm
from cteforge.domain.variables import RangeShape, Role, VariableType
from cteforge.parsing.analyze import analyze
from cteforge.templating.render import render
from cteforge.templating.roundtrip import roundtrip_check
from cteforge.templating.template import apply_annotations

D = Dialect.DUCKDB


def test_template_has_placeholders_not_values(sample_definition):
    t = sample_definition.template
    for placeholder in (
        "$period__start",
        "$period__end",
        "$regions",
        "$won_statuses",
        "$paid_status",
    ):
        assert placeholder in t
    for value in ("2026-01-01", "EMEA", "closed_won", "'paid'"):
        assert value not in t


def test_variable_specs(sample_definition):
    period = sample_definition.variable("period")
    assert period.type is VariableType.DATE_RANGE
    assert period.shape is RangeShape.HALF_OPEN
    assert period.literal_form is LiteralForm.TYPED and period.cast_type == "TIMESTAMP"
    assert period.column == "deals.closed_at" and period.column_type == "TIMESTAMP"
    assert period.original_value == {"start": "2026-01-01", "end": "2026-04-01"}
    assert sample_definition.variable("won_statuses").role is Role.FIXED


def test_same_query_back(sample_sql, sample_definition):
    report = roundtrip_check(sample_sql, sample_definition)
    assert report.identical, report.diff


def test_tampered_template_is_caught(sample_sql, sample_definition):
    tampered = sample_definition.model_copy(
        update={
            "template": sample_definition.template.replace(
                "SUM(i.paid_amount)", "MAX(i.paid_amount)"
            )
        }
    )
    report = roundtrip_check(sample_sql, tampered)
    assert not report.identical
    assert "MAX(i.paid_amount)" in report.diff


def test_swapped_range_bounds_are_rejected(sample_sql, sample_schema):
    analysis = analyze(sample_sql, D, sample_schema)
    wrong = Annotation(
        name="period",
        type=VariableType.DATE_RANGE,
        literals=["closed_deals.L2", "closed_deals.L1"],
    )
    with pytest.raises(AnnotationError, match="not a lower/upper bound pair"):
        apply_annotations(sample_sql, D, analysis, [wrong])


def test_type_must_fit_warehouse_column(sample_schema):
    sql = "SELECT * FROM deals WHERE status = 'open'"
    analysis = analyze(sql, D, sample_schema)
    wrong = Annotation(name="s", type=VariableType.DATE, literals=["main.L1"])
    with pytest.raises(AnnotationError):
        apply_annotations(sql, D, analysis, [wrong])


def test_partial_in_list_is_rejected(sample_sql, sample_schema):
    analysis = analyze(sample_sql, D, sample_schema)
    partial = Annotation(name="r", type=VariableType.TEXT_LIST, literals=["deals_in_region.L1"])
    with pytest.raises(AnnotationError, match="every literal"):
        apply_annotations(sample_sql, D, analysis, [partial])


def test_unknown_and_reused_literals(sample_sql, sample_schema):
    analysis = analyze(sample_sql, D, sample_schema)
    with pytest.raises(AnnotationError, match="unknown literal"):
        apply_annotations(
            sample_sql,
            D,
            analysis,
            [Annotation(name="x", type=VariableType.TEXT, literals=["nope.L9"])],
        )
    with pytest.raises(AnnotationError, match="used by two"):
        apply_annotations(
            sample_sql,
            D,
            analysis,
            [
                Annotation(name="a", type=VariableType.TEXT, literals=["collected.L1"]),
                Annotation(name="b", type=VariableType.TEXT, literals=["collected.L1"]),
            ],
        )


def test_render_new_values(sample_definition):
    result = render(
        sample_definition,
        {
            "period": {"start": "2026-07-01", "end": "2026-10-01"},
            "regions": ["EMEA"],
        },
    )
    assert "CAST('2026-07-01' AS TIMESTAMP)" in result.sql
    assert "IN ('EMEA')" in result.sql
    assert "'closed_won', 'closed_won_renewal'" in result.sql  # fixed logic untouched
    assert result.warnings == [] and not result.transpiled
    assert "$period" not in result.sql


def test_render_rejects_quarter_shorthand(sample_definition):
    with pytest.raises(VariableValueError, match="YYYY-MM-DD"):
        render(sample_definition, {"period": "2026-Q3"})


def test_render_rejects_unknown_variable(sample_definition):
    with pytest.raises(RenderError, match="Unknown variable"):
        render(sample_definition, {"region": ["EMEA"]})


def test_fixed_override_and_allowed_values_warn(sample_definition):
    result = render(sample_definition, {"won_statuses": ["open"], "regions": ["MARS"]})
    assert any("fixed business logic" in w for w in result.warnings)
    assert any("outside approved values" in w for w in result.warnings)


def test_values_cannot_inject_sql(sample_definition):
    evil = "EMEA') OR 1=1 --"
    result = render(sample_definition, {"regions": [evil]})
    assert "'EMEA'') OR 1=1 --'" in result.sql  # quoted as one string literal
    assert "OR 1 = 1" not in result.sql


def test_bare_literals_stay_bare():
    sql = "SELECT * FROM deals WHERE closed_at >= '2026-01-01' AND closed_at < '2026-04-01'"
    analysis = analyze(sql, D)
    annotated = apply_annotations(
        sql,
        D,
        analysis,
        [
            Annotation(
                name="period",
                type=VariableType.DATE_RANGE,
                literals=["main.L1", "main.L2"],
            )
        ],
    )
    from cteforge.domain.models import Definition

    d = Definition(
        id="x",
        title="x",
        source_dialect=D,
        template=annotated.template,
        variables=annotated.variables,
    )
    assert roundtrip_check(sql, d).identical
    assert (
        "closed_at >= '2026-07-01'"
        in render(d, {"period": {"start": "2026-07-01", "end": "2026-08-01"}}).sql
    )


@pytest.mark.parametrize("dialect", list(Dialect))
def test_template_roundtrips_in_every_source_dialect(dialect):
    """ADR 0011: each dialect writes placeholders differently; all must render back."""
    sql = (
        "SELECT * FROM t WHERE region IN ('EMEA', 'NA') AND d >= '2026-01-01' AND d < '2026-04-01'"
    )
    analysis = analyze(sql, dialect)
    annotated = apply_annotations(
        sql,
        dialect,
        analysis,
        [
            Annotation(
                name="regions",
                type=VariableType.TEXT_LIST,
                literals=["main.L1", "main.L2"],
            ),
            Annotation(
                name="period",
                type=VariableType.DATE_RANGE,
                literals=["main.L3", "main.L4"],
            ),
        ],
    )
    from cteforge.domain.models import Definition

    d = Definition(
        id="x",
        title="x",
        source_dialect=dialect,
        template=annotated.template,
        variables=annotated.variables,
    )
    assert roundtrip_check(sql, d).identical
    rendered = render(
        d, {"regions": ["APAC"], "period": {"start": "2026-07-01", "end": "2026-10-01"}}
    )
    assert "'APAC'" in rendered.sql and "'2026-07-01'" in rendered.sql
