"""Use-case: import a trusted query — analyze it, apply annotations, prove nothing changed.

This is the core of `parse_query` / `propose_definition` (DESIGN §6, §7).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from cteforge.domain.codec import AnnotationFile
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import WarehouseError
from cteforge.domain.models import Definition, DefinitionState
from cteforge.files.definitions import load_annotations, load_sql, save_definition
from cteforge.parsing.analyze import QueryAnalysis, Schema, analyze, referenced_tables
from cteforge.templating.render import render
from cteforge.templating.roundtrip import RoundTripReport, roundtrip_check
from cteforge.templating.template import apply_annotations
from cteforge.warehouse.base import Warehouse

RESULT_COMPARE_LIMIT = 10_000


class ImportOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    definition: Definition
    roundtrip: RoundTripReport
    results_match: bool | None  # None when no warehouse was available.
    problems: list[str]


def fetch_schema(sql: str, dialect: Dialect, warehouse: Warehouse | None) -> Schema:
    if warehouse is None:
        return {}
    return {t: warehouse.column_types(t) for t in referenced_tables(sql, dialect)}


def analyze_query(sql: str, dialect: Dialect, warehouse: Warehouse | None = None) -> QueryAnalysis:
    return analyze(sql, dialect, fetch_schema(sql, dialect, warehouse))


def _same_rows(a: list[tuple], b: list[tuple]) -> bool:
    return sorted(map(repr, a)) == sorted(map(repr, b))


def import_query(
    sql: str,
    dialect: Dialect,
    annotations: AnnotationFile,
    warehouse: Warehouse | None = None,
) -> ImportOutcome:
    """Build a definition from a trusted query + annotations and run the checks.

    State is LLM_ANNOTATED when every check passes, else DRAFT. Never VERIFIED:
    only a person can verify (ADR 0007).
    """
    analysis = analyze_query(sql, dialect, warehouse)
    annotated = apply_annotations(sql, dialect, analysis, annotations.variables)
    definition = Definition(
        id=annotations.id,
        title=annotations.title,
        meaning=annotations.meaning,
        keywords=annotations.keywords,
        use_when=annotations.use_when,
        dont_use_when=annotations.dont_use_when,
        grain=annotations.grain,
        source_dialect=dialect,
        template=annotated.template,
        variables=annotated.variables,
        tables=analysis.tables,
        outputs=analysis.outputs,
    )

    problems: list[str] = []
    report = roundtrip_check(sql, definition)
    if not report.identical:
        problems.append("Rendering with the original values does not reproduce the query.")

    results_match: bool | None = None
    if warehouse is not None:
        try:
            original = warehouse.run(sql, RESULT_COMPARE_LIMIT)
            rendered = warehouse.run(render(definition, {}).sql, RESULT_COMPARE_LIMIT)
            results_match = (
                original.columns == rendered.columns
                and not original.truncated
                and _same_rows(original.rows, rendered.rows)
            )
            if original.truncated:
                problems.append("Result too large to compare fully.")
            elif not results_match:
                problems.append("Results differ between the original and the template.")
        except WarehouseError as exc:
            results_match = False
            problems.append(str(exc))

    state = DefinitionState.DRAFT if problems else DefinitionState.LLM_ANNOTATED
    return ImportOutcome(
        definition=definition.model_copy(update={"state": state}),
        roundtrip=report,
        results_match=results_match,
        problems=problems,
    )


def analyze_file(
    sql_path: str, dialect: Dialect, warehouse: Warehouse | None = None
) -> QueryAnalysis:
    return analyze_query(load_sql(sql_path), dialect, warehouse)


def import_files(
    sql_path: str,
    annotations_path: str,
    dialect: Dialect,
    warehouse: Warehouse | None = None,
    out_path: str | None = None,
) -> ImportOutcome:
    """File-based wrapper used by the CLI. Writes the definition only if checks pass."""
    outcome = import_query(
        load_sql(sql_path), dialect, load_annotations(annotations_path), warehouse
    )
    if out_path and not outcome.problems:
        save_definition(out_path, outcome.definition)
    return outcome
