"""Use-case: re-validate definitions against their warehouses (drift check; DESIGN §10, §18).

For the latest version of each definition (all, or `ids`), the import checks (DESIGN §7)
are re-run with the checks engine in `import_query` — nothing is re-implemented here:
1. The trusted query and the analyst's annotations are rebuilt from the stored definition
   (`_library.original_sql`, `_library.annotation_file`: same literal / grain IDs).
2. `import_query` then re-analyzes the query against the CURRENT warehouse schema, re-applies
   the annotations (type compatibility per variable), runs the same-query-back check,
   EXPLAINs the rendered template and compares its results with the rebuilt original.
Drift is reported, never acted on: no state, version or proposal changes (DESIGN §18).

`drift` issues (one per finding; the engine's own issues follow them in `issues`):
- error: the checks no longer pass (any error issue: compile failure, type mismatch,
  results differ, template no longer renders, dialect mismatch, ...).
- warning: a variable's compared column type changed (e.g. TIMESTAMP → DATE) or can no
  longer be read (`type_unknown`: column or table gone), or checks could not complete.
A definition is `ok` when it has no `drift` issue. Without a usable profile (none
configured, unknown name) it is `checked=False` with a `checks_incomplete` warning.

Permissions: re-validating named `ids` needs Action.RUN (viewer: it only reads, like a
run). Re-validating the whole library (`ids=None`) runs every definition's query twice on
the warehouses, so it needs an approver or admin (the APPROVE role minimum). Audit: one
`revalidate` event per call (fail closed), listing the definitions checked and those that
drifted; never SQL text. Refusals and unknown ids are recorded by `_audit.audit_failures`.
Returns `cards.DefinitionDrift` per definition.

Cost: like import, each definition's query runs twice (original vs template), capped at
`import_query.RESULT_COMPARE_LIMIT` rows.
"""

from __future__ import annotations

from cteforge.domain.audit import AuditKind
from cteforge.domain.errors import (
    CteforgeError,
    NotFoundError,
    PermissionDeniedError,
    PolicyError,
)
from cteforge.domain.issues import Issue, IssueCode, Severity, has_errors
from cteforge.domain.models import Definition
from cteforge.domain.users import User
from cteforge.services import _audit, _library
from cteforge.services.auth import permitted, require
from cteforge.services.cards import DefinitionDrift
from cteforge.services.context import AppContext
from cteforge.services.import_query import import_query
from cteforge.trust.approval import Action


def _drift(severity: Severity, message: str, variable: str | None = None) -> Issue:
    return Issue(code=IssueCode.DRIFT, severity=severity, message=message, variable=variable)


def _check(ctx: AppContext, definition: Definition) -> DefinitionDrift:
    base = {"id": definition.id, "version": definition.version, "state": definition.state}
    try:
        profile = ctx.warehouses.resolve_name(definition.profile)
    except (PolicyError, NotFoundError) as exc:
        issue = Issue(
            code=IssueCode.CHECKS_INCOMPLETE,
            severity=Severity.WARNING,
            message=f"Not checked: {exc}",
        )
        return DefinitionDrift(
            **base, profile=definition.profile, checked=False, ok=True, issues=[issue]
        )
    try:
        outcome = import_query(
            _library.original_sql(definition),
            definition.source_dialect,
            _library.annotation_file(definition),
            ctx.warehouses.get(profile),
        )
    except CteforgeError as exc:
        issue = _drift(
            Severity.ERROR,
            f"The definition can no longer be checked on profile '{profile}' "
            f"({type(exc).__name__}).",
        )
        return DefinitionDrift(**base, profile=profile, checked=True, ok=False, issues=[issue])

    drift: list[Issue] = []
    if has_errors(outcome.issues):
        codes = sorted({i.code.value for i in outcome.issues if i.severity is Severity.ERROR})
        drift.append(
            _drift(Severity.ERROR, f"The import checks no longer pass ({', '.join(codes)}).")
        )
    for code in (IssueCode.TYPE_UNKNOWN, IssueCode.CHECKS_INCOMPLETE):
        for issue in outcome.issues:
            if issue.code is code:
                drift.append(_drift(Severity.WARNING, issue.message, issue.variable))
    fresh = {v.name: v for v in outcome.definition.variables}
    for spec in definition.variables:
        now = fresh.get(spec.name)
        if now is None or spec.column_type is None or now.column_type is None:
            continue
        if now.column_type.lower() != spec.column_type.lower():
            drift.append(
                _drift(
                    Severity.WARNING,
                    f"Column type of '{spec.name}' ({spec.column}) changed from "
                    f"{spec.column_type} to {now.column_type}.",
                    spec.name,
                )
            )
    return DefinitionDrift(
        **base,
        profile=profile,
        checked=True,
        ok=not drift,
        results_match=outcome.results_match,
        issues=[*drift, *outcome.issues],
    )


def revalidate(ctx: AppContext, user: User, ids: list[str] | None = None) -> list[DefinitionDrift]:
    """Drift report for the latest version of each definition (or of `ids`, in that order).
    Raises NotFoundError for an unknown id. Never changes anything."""
    scope = "all" if ids is None else "ids"
    with _audit.audit_failures(ctx, user, AuditKind.REVALIDATE, extra={"scope": scope}):
        require(user, Action.RUN)
        if ids is None and not permitted(user, Action.APPROVE):
            raise PermissionDeniedError(
                f"User '{user.name}' may not re-validate the whole library (approver or "
                "admin only); pass the ids to check."
            )
        if ids is None:
            definitions = ctx.store.list_definitions()
        else:
            definitions = []
            for id in dict.fromkeys(ids):
                found = ctx.store.get_definition(id)
                if found is None:
                    raise NotFoundError(f"Definition '{id}' does not exist")
                definitions.append(found)
    report = [_check(ctx, d) for d in definitions]
    _audit.record(
        ctx,
        user,
        AuditKind.REVALIDATE,
        definitions=[f"{r.id}@{r.version}" for r in report],
        extra={
            "checked": sum(r.checked for r in report),
            "drifted": [r.id for r in report if not r.ok],
            "scope": scope,
        },
    )
    return report
