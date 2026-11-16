"""Use-case: parse a trusted query for annotation (DESIGN §6 "parse_query").

Reports literals with IDs, structure, range/grain suggestions and joins. Column types come
from the warehouse of `profile` (or the default profile); otherwise there are no types and
an issue says why:
- profile dialect differs → `dialect_mismatch` (warning): types are still read from it, and
  the proposal's checks will run transpiled to its dialect (import_query, ADR 0013);
- no profile configured → `checks_incomplete` (warning);
- the schema lookup fails → `checks_incomplete` (warning; error class only, no driver text);
- a table the warehouse does not know → `type_unknown` (warning).

Needs Action.DESCRIBE (viewer): parsing only reads table schemas, it changes nothing. Audit
is best-effort (`fail_closed=False`) and keeps only the SQL's SHA-256. The schema lookup
reuses the import checks engine (`import_query.fetch_schema`), which DESIGN/services
CLAUDE.md name as the engine behind parse_query.
"""

from __future__ import annotations

from cteforge.domain.audit import AuditKind
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import (
    PolicyError,
    WarehouseError,
)
from cteforge.domain.issues import Issue, IssueCode, Severity
from cteforge.domain.users import User
from cteforge.parsing.analyze import Schema, analyze
from cteforge.services import _audit, _library
from cteforge.services.auth import require
from cteforge.services.cards import ParseReport, parse_report
from cteforge.services.context import AppContext
from cteforge.services.import_query import fetch_schema
from cteforge.trust.approval import Action


def _issue(code: IssueCode, message: str) -> Issue:
    return Issue(code=code, severity=Severity.WARNING, message=message)


def _schema(
    ctx: AppContext, sql: str, dialect: Dialect, profile: str | None
) -> tuple[Schema, str | None, list[Issue]]:
    """(schema, profile used, issues). Raises NotFoundError for an unknown named profile."""
    try:
        name = ctx.warehouses.resolve_name(profile)
    except PolicyError:  # no profiles, or several and no default (a bad name: NotFound)
        return (
            {},
            None,
            [
                _issue(
                    IssueCode.CHECKS_INCOMPLETE,
                    "No single warehouse profile to read column types from; pass a "
                    "profile. Literals are reported without column types.",
                )
            ],
        )
    summary = ctx.warehouses.summary(name)
    mismatch: list[Issue] = []
    if summary.dialect is not dialect:
        mismatch = [
            _issue(
                IssueCode.DIALECT_MISMATCH,
                f"The SQL is {dialect} but profile '{name}' is {summary.dialect}: column "
                "types were read from it, and the proposal's checks will run on the SQL "
                f"transpiled to {summary.dialect} (labeled, lower trust; ADR 0013). Use a "
                "profile of the same dialect for native checks.",
            )
        ]
    try:
        schema = fetch_schema(sql, dialect, ctx.warehouses.get(name))
    except WarehouseError as exc:
        return (
            {},
            name,
            [
                *mismatch,
                _issue(
                    IssueCode.CHECKS_INCOMPLETE,
                    f"Could not read column types from profile '{name}' "
                    f"({type(exc).__name__}); literals are reported without types.",
                ),
            ],
        )
    issues = mismatch + [
        _issue(
            IssueCode.TYPE_UNKNOWN,
            f"Table '{table}' was not found in profile '{name}'; its column types are unknown.",
        )
        for table, columns in schema.items()
        if not columns
    ]
    return {t: c for t, c in schema.items() if c}, name, issues


def parse_query(
    ctx: AppContext, user: User, sql: str, dialect: Dialect, profile: str | None = None
) -> ParseReport:
    """Analyze `sql` (one statement in `dialect`). Raises SqlParseError for SQL that does
    not parse, NotFoundError for an unknown profile name, PolicyError for an unknown
    dialect."""
    with _audit.audit_failures(ctx, user, AuditKind.PARSE_QUERY, sql=sql):
        require(user, Action.DESCRIBE)
        dialect = _library.parse_dialect(dialect)
    used: str | None = profile
    fields = {"sql": sql, "extra": {"dialect": dialect.value, "profile": profile}}
    with _audit.audit_failures(ctx, user, AuditKind.PARSE_QUERY, **fields):
        schema, used, issues = _schema(ctx, sql, dialect, profile)
        analysis = analyze(sql, dialect, schema)
    report = parse_report(analysis, schema_found=bool(schema), issues=issues)
    _audit.record(
        ctx,
        user,
        AuditKind.PARSE_QUERY,
        fail_closed=False,
        sql=sql,
        extra={
            "dialect": dialect.value,
            "profile": used,
            "literals": len(report.literals),
            "schema_found": report.schema_found,
        },
    )
    return report
