"""Template + values → SQL for a target dialect. Deterministic; no LLM involved (ADR 0005)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlglot import exp

from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import RenderError
from cteforge.domain.models import Definition
from cteforge.domain.variables import Role, TypedValue, to_plain, validate_value
from cteforge.parsing.analyze import parse
from cteforge.templating.literals import Replacement, build_replacements


class RenderResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    sql: str
    dialect: Dialect
    values: dict[str, Any]  # Plain values actually used (incl. defaults).
    warnings: list[str]
    transpiled: bool  # True when target dialect != source dialect (lower trust, ADR 0013).


def _resolve_values(
    definition: Definition, values: dict[str, Any]
) -> tuple[dict[str, TypedValue], list[str]]:
    unknown = set(values) - {v.name for v in definition.variables}
    if unknown:
        known = ", ".join(v.name for v in definition.variables) or "none"
        raise RenderError(f"Unknown variable(s): {', '.join(sorted(unknown))}. Known: {known}")
    typed: dict[str, TypedValue] = {}
    warnings: list[str] = []
    for spec in definition.variables:
        if spec.name in values:
            raw = values[spec.name]
            if spec.role is Role.FIXED and raw != spec.original_value:
                warnings.append(
                    f"'{spec.name}' is fixed business logic; overriding it means the "
                    "result is no longer the verified definition."
                )
        else:
            raw = spec.original_value
        value = validate_value(spec.name, spec.type, raw)
        if spec.allowed is not None:
            plain = to_plain(spec.type, value)
            outside = [
                v for v in (plain if isinstance(plain, list) else [plain]) if v not in spec.allowed
            ]
            if outside:
                warnings.append(
                    f"'{spec.name}': {outside} outside approved values {spec.allowed}; "
                    "results may be empty or unapproved."
                )
        typed[spec.name] = value
    return typed, warnings


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


def render_tree(
    definition: Definition, values: dict[str, Any]
) -> tuple[exp.Expression, dict[str, Any], list[str]]:
    """Render into a sqlglot tree in the source dialect (used by render and roundtrip)."""
    typed, warnings = _resolve_values(definition, values)
    replacements: dict[str, Replacement] = {}
    for spec in definition.variables:
        replacements.update(build_replacements(spec, typed[spec.name], definition.source_dialect))

    tree = parse(definition.template, definition.source_dialect)
    for placeholder, name in template_slots(tree):
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
    plain = {s.name: to_plain(s.type, typed[s.name]) for s in definition.variables}
    return tree, plain, warnings


def render(
    definition: Definition, values: dict[str, Any], dialect: Dialect | None = None
) -> RenderResult:
    target = dialect or definition.source_dialect
    tree, plain, warnings = render_tree(definition, values)
    transpiled = target is not definition.source_dialect
    if transpiled:
        warnings.append(
            f"Transpiled from {definition.source_dialect} to {target}; review before "
            "trusting (ADR 0013)."
        )
    return RenderResult(
        sql=tree.sql(dialect=str(target), pretty=True),
        dialect=target,
        values=plain,
        warnings=warnings,
        transpiled=transpiled,
    )
