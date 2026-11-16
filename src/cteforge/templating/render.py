"""Template + values → SQL for a target dialect. Deterministic; no LLM involved (ADR 0005)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlglot import exp

from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import RenderError
from cteforge.domain.issues import Issue, IssueCode, Severity
from cteforge.domain.models import Definition, VariableSpec
from cteforge.domain.variables import Role, TypedValue, to_plain, validate_value
from cteforge.parsing.analyze import parse
from cteforge.templating.literals import Replacement, build_replacements


class RenderResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    sql: str
    dialect: Dialect
    values: dict[str, Any]  # Plain values actually used (incl. defaults).
    issues: list[Issue]  # fixed_override, value_not_allowed, transpiled (all warnings).
    transpiled: bool  # True when target dialect != source dialect (lower trust, ADR 0013).


def _value_issues(spec: VariableSpec, value: TypedValue, overridden: bool) -> list[Issue]:
    issues: list[Issue] = []
    if overridden and spec.role is Role.FIXED:
        issues.append(
            Issue(
                code=IssueCode.FIXED_OVERRIDE,
                severity=Severity.WARNING,
                message=f"'{spec.name}' is fixed business logic; overriding it means the "
                "result is no longer the verified definition.",
                variable=spec.name,
            )
        )
    if spec.allowed is not None:
        plain = to_plain(spec.type, value)
        outside = [
            v for v in (plain if isinstance(plain, list) else [plain]) if v not in spec.allowed
        ]
        if outside:
            issues.append(
                Issue(
                    code=IssueCode.VALUE_NOT_ALLOWED,
                    severity=Severity.WARNING,
                    message=f"'{spec.name}': {outside} outside approved values "
                    f"{spec.allowed}; results may be empty or unapproved.",
                    variable=spec.name,
                )
            )
    return issues


def _resolve_values(
    definition: Definition, values: dict[str, Any]
) -> tuple[dict[str, TypedValue], list[Issue]]:
    unknown = set(values) - {v.name for v in definition.variables}
    if unknown:
        known = ", ".join(v.name for v in definition.variables) or "none"
        raise RenderError(f"Unknown variable(s): {', '.join(sorted(unknown))}. Known: {known}")
    typed: dict[str, TypedValue] = {}
    issues: list[Issue] = []
    for spec in definition.variables:
        original = validate_value(spec.name, spec.type, spec.original_value)
        value = (
            validate_value(spec.name, spec.type, values[spec.name])
            if spec.name in values
            else original
        )
        issues += _value_issues(spec, value, overridden=value != original)
        typed[spec.name] = value
    return typed, issues


def template_slots(tree: exp.Expression) -> list[tuple[exp.Expression, str]]:
    """Named placeholders in a parsed template, as (node, name).

    Each dialect writes named placeholders its own way when the template is stored
    (`$x` DuckDB, `:x` Snowflake/Databricks, `%(x)s` Postgres, `@x` BigQuery). Most parse
    back as exp.Placeholder; BigQuery's `@x` parses back as exp.Parameter. ADR 0011.
    """
    slots: list[tuple[exp.Expression, str]] = []
    for node in tree.find_all(exp.Placeholder, exp.Parameter):
        if isinstance(node, exp.Placeholder) and node.name:
            slots.append((node, node.name))
        elif isinstance(node, exp.Parameter) and isinstance(node.this, exp.Var):
            slots.append((node, node.this.name))
    return slots


def _fill(tree: exp.Expression, replacements: dict[str, Replacement]) -> None:
    present = template_slots(tree)
    missing = set(replacements) - {name for _, name in present}
    if missing:
        raise RenderError(f"Declared slot(s) not in the template: {', '.join(sorted(missing))}")
    for placeholder, name in present:
        if name not in replacements:
            raise RenderError(f"Template placeholder :{name} has no declared variable")
        node = replacements[name]
        if isinstance(node, list):
            parent = placeholder.parent
            if not isinstance(parent, exp.In):
                raise RenderError(f"List placeholder :{name} must sit inside IN (...)")
            parent.set("expressions", [n.copy() for n in node])
        else:
            placeholder.replace(node.copy())
    if template_slots(tree) or any(True for _ in tree.find_all(exp.Placeholder)):
        raise RenderError("Rendered SQL still contains placeholders")  # pragma: no cover


def render_tree(
    definition: Definition, values: dict[str, Any], target: Dialect | None = None
) -> tuple[exp.Expression, dict[str, Any], list[Issue]]:
    """Render into a sqlglot tree in the source dialect (used by render and roundtrip).

    `target` only changes how typed literals are cast for a transpile (DESIGN §13).
    """
    typed, issues = _resolve_values(definition, values)
    replacements: dict[str, Replacement] = {}
    for spec in definition.variables:
        replacements.update(
            build_replacements(spec, typed[spec.name], definition.source_dialect, target)
        )
    tree = parse(definition.template, definition.source_dialect)
    _fill(tree, replacements)
    plain = {s.name: to_plain(s.type, typed[s.name]) for s in definition.variables}
    return tree, plain, issues


def render(
    definition: Definition, values: dict[str, Any], dialect: Dialect | None = None
) -> RenderResult:
    target = dialect or definition.source_dialect
    tree, plain, issues = render_tree(definition, values, target)
    transpiled = target is not definition.source_dialect
    if transpiled:
        issues.append(
            Issue(
                code=IssueCode.TRANSPILED,
                severity=Severity.WARNING,
                message=f"Transpiled from {definition.source_dialect} to {target}; review "
                "before trusting (ADR 0013).",
            )
        )
    return RenderResult(
        sql=tree.sql(dialect=str(target), pretty=True),
        dialect=target,
        values=plain,
        issues=issues,
        transpiled=transpiled,
    )
