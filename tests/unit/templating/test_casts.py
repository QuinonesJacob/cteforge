"""Typed literals keep the source type's semantics when transpiled (DESIGN §13)."""

import pytest

from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import RenderError
from cteforge.domain.models import Annotation, Definition, LiteralForm, VariableSlot, VariableSpec
from cteforge.domain.variables import Role, VariableType
from cteforge.parsing.analyze import analyze
from cteforge.templating.literals import cast_type
from cteforge.templating.render import render
from cteforge.templating.template import apply_annotations

PG, RS, SF, BQ, DB, DUCK = (
    Dialect.POSTGRES,
    Dialect.REDSHIFT,
    Dialect.SNOWFLAKE,
    Dialect.BIGQUERY,
    Dialect.DATABRICKS,
    Dialect.DUCKDB,
)


def _definition(sql: str, dialect: Dialect) -> Definition:
    ann = Annotation(name="since", type=VariableType.DATE, literals=["main.L1"])
    annotated = apply_annotations(sql, dialect, analyze(sql, dialect), [ann])
    return Definition(
        id="x",
        title="x",
        source_dialect=dialect,
        template=annotated.template,
        variables=annotated.variables,
    )


@pytest.mark.parametrize(
    ("source", "literal", "target", "expected"),
    [
        # zone-naive source types stay naive in every target
        (PG, "TIMESTAMP '2026-01-01'", SF, "CAST('2026-01-01' AS TIMESTAMPNTZ)"),
        (PG, "TIMESTAMP '2026-01-01'", DB, "CAST('2026-01-01' AS TIMESTAMP_NTZ)"),
        (PG, "TIMESTAMP '2026-01-01'", BQ, "CAST('2026-01-01' AS DATETIME)"),
        (BQ, "DATETIME '2026-01-01'", SF, "CAST('2026-01-01' AS TIMESTAMPNTZ)"),
        (BQ, "DATETIME '2026-01-01'", DB, "CAST('2026-01-01' AS TIMESTAMP_NTZ)"),
        (DUCK, "TIMESTAMP '2026-01-01'", BQ, "CAST('2026-01-01' AS DATETIME)"),
        # zone-aware source types stay zone-aware
        (BQ, "TIMESTAMP '2026-01-01'", SF, "CAST('2026-01-01' AS TIMESTAMPTZ)"),
        (DUCK, "TIMESTAMPTZ '2026-01-01'", BQ, "CAST('2026-01-01' AS TIMESTAMP)"),
        (SF, "'2026-01-01'::TIMESTAMP_TZ", PG, "CAST('2026-01-01' AS TIMESTAMPTZ)"),
        # Redshift: TIMESTAMP is naive, TIMESTAMPTZ zone-aware, both ways (ADR 0022)
        (RS, "TIMESTAMP '2026-01-01'", SF, "CAST('2026-01-01' AS TIMESTAMPNTZ)"),
        (RS, "TIMESTAMP '2026-01-01'", BQ, "CAST('2026-01-01' AS DATETIME)"),
        (RS, "'2026-01-01'::TIMESTAMPTZ", BQ, "CAST('2026-01-01' AS TIMESTAMP)"),
        (RS, "'2026-01-01'::TIMESTAMPTZ", SF, "CAST('2026-01-01' AS TIMESTAMPTZ)"),
        (BQ, "DATETIME '2026-01-01'", RS, "CAST('2026-01-01' AS TIMESTAMP)"),
        (BQ, "TIMESTAMP '2026-01-01'", RS, "CAST('2026-01-01' AS TIMESTAMP WITH TIME ZONE)"),
        (SF, "'2026-01-01'::TIMESTAMP_NTZ", RS, "CAST('2026-01-01' AS TIMESTAMP)"),
    ],
)
def test_transpiled_casts_keep_time_zone_semantics(source, literal, target, expected):
    definition = _definition(f"SELECT * FROM t WHERE ts >= {literal}", source)
    assert expected in render(definition, {"since": "2026-01-01"}, target).sql


def test_native_render_keeps_the_type_as_written():
    definition = _definition("SELECT * FROM t WHERE ts >= TIMESTAMP '2026-01-01'", SF)
    assert "CAST('2026-01-01' AS TIMESTAMP)" in render(definition, {}).sql


def _typed_spec(cast: str | None, column_type: str | None) -> tuple[VariableSlot, VariableSpec]:
    slot = VariableSlot(placeholder="d", literal_form=LiteralForm.TYPED, cast_type=cast)
    spec = VariableSpec(
        name="d",
        type=VariableType.DATE,
        role=Role.FIXED,
        column_type=column_type,
        original_value="2026-01-01",
        slots=[slot],
    )
    return slot, spec


def test_cast_falls_back_to_the_column_type():
    slot, spec = _typed_spec("NOT A ( TYPE", "DATE")
    assert cast_type(slot, spec, DUCK).sql("duckdb") == "DATE"
    slot, spec = _typed_spec(None, None)
    with pytest.raises(RenderError, match="no usable type"):
        cast_type(slot, spec, DUCK)
