"""Use-case: import a trusted query — analyze it, apply annotations, prove nothing changed.

This is the checks engine behind `parse_query` / `propose_definition` (DESIGN §6, §7).

State rule (DESIGN §8): the definition is LLM_ANNOTATED only when there is no error issue
AND the warehouse checks actually ran — results compared and every compared type known.
Otherwise it stays DRAFT (with `checks_incomplete` saying what did not run). Never VERIFIED:
only a person can verify (ADR 0007).

Another dialect (DESIGN §12, ADR 0013): when the warehouse's dialect is not the SQL's, the
checks still run, on SQL transpiled to the warehouse's dialect (the original query and the
template rendered with its original values, both transpiled), and a `dialect_mismatch`
warning says so. Column types are read from that warehouse. The definition keeps its
source dialect; running it on that profile transpiles it again and lowers the label.

Check runs never hand back the warehouse's runtime error text (`_run_failure`): it can
quote data values, and proposal issues are stored and readable by any viewer, also in
strict mode where exploratory queries are refused. EXPLAIN (compile) errors are kept.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict

from cteforge.domain.codec import AnnotationFile
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import WarehouseError
from cteforge.domain.issues import Issue, IssueCode, Severity, has_errors
from cteforge.domain.models import Definition, DefinitionState
from cteforge.files.definitions import load_annotations, load_sql, save_definition
from cteforge.parsing.analyze import (
    QueryAnalysis,
    Schema,
    analyze,
    referenced_tables,
    transpile,
)
from cteforge.templating.render import render
from cteforge.templating.roundtrip import RoundTripReport, roundtrip_check
from cteforge.templating.template import apply_annotations
from cteforge.warehouse.base import Warehouse

RESULT_COMPARE_LIMIT = 10_000


class ImportOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    definition: Definition
    roundtrip: RoundTripReport
    results_match: bool | None  # None when the results were not compared.
    issues: list[Issue]


def _issue(code: IssueCode, severity: Severity, message: str) -> Issue:
    return Issue(code=code, severity=severity, message=message)


def fetch_schema(sql: str, dialect: Dialect, warehouse: Warehouse | None) -> Schema:
    if warehouse is None:
        return {}
    return {t: warehouse.column_types(t) for t in referenced_tables(sql, dialect)}


def analyze_query(sql: str, dialect: Dialect, warehouse: Warehouse | None = None) -> QueryAnalysis:
    return analyze(sql, dialect, fetch_schema(sql, dialect, warehouse))


def _same_rows(a: list[tuple], b: list[tuple]) -> bool:
    return sorted(map(repr, a)) == sorted(map(repr, b))


def _dialect_issues(dialect: Dialect, warehouse: Warehouse | None) -> list[Issue]:
    """A `dialect_mismatch` warning when the checks run transpiled (ADR 0013)."""
    if warehouse is None or warehouse.dialect is dialect:
        return []
    return [
        _issue(
            IssueCode.DIALECT_MISMATCH,
            Severity.WARNING,
            f"The SQL is {dialect} but the warehouse is {warehouse.dialect}: the checks ran "
            f"on the SQL transpiled to {warehouse.dialect}, and runs on this warehouse are "
            "transpiled too (labeled, lower trust). Import against a warehouse of the same "
            "dialect for native checks.",
        )
    ]


# "<context>: <Class> Error: ..." as connectors word it (DuckDB: "Conversion Error"); only
# the head of the message is read, never the part that may quote values.
_ERROR_CLASS = re.compile(
    r"(?:[A-Za-z ]{1,40}: )?([A-Z][A-Za-z]{0,30}(?: [A-Z][A-Za-z]{0,30}){0,3} Error):"
)


def _run_failure(exc: WarehouseError) -> Issue:
    """A check run that failed while EXECUTING, reported without the driver's message.

    Runtime errors can quote data values (a failed cast prints the value it could not
    convert), and these issues are stored on proposals any viewer can read. So a
    proposal's checks would be a way to read table contents the team mode does not let the
    proposer query (strict mode refuses check_and_run). Only the error class is kept (e.g.
    "Conversion Error"); compile errors (EXPLAIN, before anything reads a row) keep their
    message, which names columns, not values."""
    found = _ERROR_CLASS.match(str(exc))
    kind = found.group(1) if found else "an error"
    return _issue(
        IssueCode.COMPILE_FAILED,
        Severity.ERROR,
        f"The query compiled but failed when run on the warehouse ({kind}). The driver's "
        "message is not shown here because it can contain data values; check the query "
        "in the warehouse directly to see it.",
    )


def _compare_results(
    sql: str, definition: Definition, warehouse: Warehouse
) -> tuple[bool | None, list[Issue]]:
    """Run the original and the rendered template, both in the warehouse's dialect
    (transpiled when the SQL's differs); (results identical?, issues)."""
    target = warehouse.dialect
    source = definition.source_dialect
    original_sql = sql if target is source else transpile(sql, source, target)
    try:
        rendered_sql = render(definition, {}, target).sql
        warehouse.explain(rendered_sql)
    except WarehouseError as exc:
        return False, [_issue(IssueCode.COMPILE_FAILED, Severity.ERROR, str(exc))]
    try:
        original = warehouse.run(original_sql, RESULT_COMPARE_LIMIT)
        rendered = warehouse.run(rendered_sql, RESULT_COMPARE_LIMIT)
    except WarehouseError as exc:
        return False, [_run_failure(exc)]
    if original.truncated or rendered.truncated:
        message = f"Result has more than {RESULT_COMPARE_LIMIT} rows; results were not compared."
        return None, [_issue(IssueCode.CHECKS_INCOMPLETE, Severity.WARNING, message)]
    if original.columns == rendered.columns and _same_rows(original.rows, rendered.rows):
        return True, []
    message = "Results differ between the original query and the template."
    return False, [_issue(IssueCode.ROUNDTRIP_RESULTS_DIFFER, Severity.ERROR, message)]


def _state(issues: list[Issue], results_match: bool | None) -> DefinitionState:
    incomplete = any(
        i.code in (IssueCode.TYPE_UNKNOWN, IssueCode.CHECKS_INCOMPLETE) for i in issues
    )
    if has_errors(issues) or incomplete or results_match is not True:
        return DefinitionState.DRAFT
    return DefinitionState.LLM_ANNOTATED


def import_query(
    sql: str,
    dialect: Dialect,
    annotations: AnnotationFile,
    warehouse: Warehouse | None = None,
) -> ImportOutcome:
    """Build a definition from a trusted query + annotations and run the checks (DESIGN §7).

    Raises AnnotationError / SqlParseError when no template can be built at all.
    """
    issues = _dialect_issues(dialect, warehouse)
    analysis = analyze_query(sql, dialect, warehouse)
    annotated = apply_annotations(sql, dialect, analysis, annotations.variables)
    issues += annotated.issues
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

    report = roundtrip_check(sql, definition)
    if not report.identical:
        message = "Rendering with the original values does not reproduce the query (see diff)."
        issues.append(_issue(IssueCode.ROUNDTRIP_SQL_DIFFERS, Severity.ERROR, message))

    results_match: bool | None = None
    if warehouse is not None and report.identical:
        results_match, run_issues = _compare_results(sql, definition, warehouse)
        issues += run_issues
    elif warehouse is None:
        message = "No warehouse: column types and query results were not checked."
        issues.append(_issue(IssueCode.CHECKS_INCOMPLETE, Severity.WARNING, message))

    return ImportOutcome(
        definition=definition.model_copy(update={"state": _state(issues, results_match)}),
        roundtrip=report,
        results_match=results_match,
        issues=issues,
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
    """File-based wrapper used by the CLI. Writes the definition only when no issue is an
    error (it may still be a draft; the file records the state)."""
    outcome = import_query(
        load_sql(sql_path), dialect, load_annotations(annotations_path), warehouse
    )
    if out_path and not has_errors(outcome.issues):
        save_definition(out_path, outcome.definition)
    return outcome
