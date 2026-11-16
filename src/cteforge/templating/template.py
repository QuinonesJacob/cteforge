"""Annotations + trusted SQL → template text with named placeholders (ADR 0011, 0014, 0015).

The analyst decides what is a variable; this module checks the decision against the SQL's
structure and reports what it finds as Issues. It raises AnnotationError only when no
template can be built (unknown/reused literal IDs, wrong arity, values that differ between
places, a format that can't read the original value, ...). Problems that still allow a
template — type mismatches, unknown types, missing descriptions — are Issues, so an
analyst sees all of them at once and the definition stays draft while any is an error.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlglot import exp

from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import AnnotationError, VariableValueError
from cteforge.domain.issues import Issue, IssueCode, Severity
from cteforge.domain.models import Annotation, LiteralForm, VariableSlot, VariableSpec
from cteforge.domain.variables import (
    PLACEHOLDER_FIELDS,
    RangeShape,
    Role,
    VariableType,
    number_from_text,
    placeholder_name,
    read_date,
    to_plain,
    validate_value,
    write_date,
)
from cteforge.parsing.analyze import (
    FoundLiteral,
    LiteralContext,
    LiteralInfo,
    QueryAnalysis,
    find_literals,
    parse,
)
from cteforge.parsing.types import compatible_variable_types

_DATE_TYPES = (VariableType.DATE, VariableType.DATE_RANGE)
# Structural facts that make a variable's name alone misleading (ADR 0015).
_NEEDS_DESCRIPTION = {
    LiteralContext.NEGATED,
    LiteralContext.OR_BRANCH,
    LiteralContext.CASE_CONDITION,
    LiteralContext.OUTER_JOIN_ON,
}


class AnnotatedTemplate(BaseModel):
    model_config = ConfigDict(frozen=True)

    template: str
    variables: list[VariableSpec]
    issues: list[Issue]


@dataclass(frozen=True)
class _Place:
    """One occurrence of the variable's filter: its literals, slots and original value."""

    infos: list[LiteralInfo]
    slots: list[VariableSlot]
    value: Any  # Comparable typed value (date, Decimal, tuple of dates, list of str, ...).


def _issue(
    code: IssueCode, severity: Severity, message: str, name: str, literal: str | None = None
) -> Issue:
    return Issue(code=code, severity=severity, message=message, variable=name, literal=literal)


# ── Checks on the annotation itself ─────────────────────────────────────────────────


def _check_options(annotation: Annotation) -> None:
    name, vtype = annotation.name, annotation.type
    if annotation.format is not None and vtype not in _DATE_TYPES:
        raise AnnotationError(f"'{name}': format applies to date and date_range only, not {vtype}")
    if annotation.format is not None and "%" not in annotation.format:
        raise AnnotationError(
            f"'{name}': format {annotation.format!r} is not a strftime pattern (e.g. '%m-%d-%Y')"
        )
    if annotation.unit is not None and vtype is not VariableType.NUMBER:
        raise AnnotationError(f"'{name}': unit applies to number only, not {vtype}")


def _resolve_infos(
    annotation: Annotation, analysis: QueryAnalysis, found: dict[str, FoundLiteral], used: set[str]
) -> list[LiteralInfo]:
    infos: list[LiteralInfo] = []
    for literal_id in annotation.literals:
        info = analysis.literal(literal_id)
        if info is None or literal_id not in found:
            known = ", ".join(lit.id for lit in analysis.literals)
            raise AnnotationError(
                f"'{annotation.name}': unknown literal {literal_id}. Known: {known}"
            )
        if literal_id in used:
            raise AnnotationError(f"Literal {literal_id} is used by two variables")
        used.add(literal_id)
        infos.append(info)
    return infos


# ── Grouping literals into places (ADR 0015) ────────────────────────────────────────


def _in_list_places(
    name: str, infos: list[LiteralInfo], analysis: QueryAnalysis, found: dict[str, FoundLiteral]
) -> list[list[LiteralInfo]]:
    groups: list[list[LiteralInfo]] = []
    for info in infos:
        if info.in_list is None:
            raise AnnotationError(f"'{name}' (text_list): {info.id} is not part of an IN list")
        if groups and groups[-1][0].in_list == info.in_list:
            groups[-1].append(info)
        else:
            groups.append([info])
    for group in groups:
        members = [lit.id for lit in analysis.literals if lit.in_list == group[0].in_list]
        if [i.id for i in group] != members:
            raise AnnotationError(
                f"'{name}' (text_list) must list every literal of {group[0].in_list} in "
                f"order: {members}"
            )
        predicate = found[group[0].id].predicate
        if len(predicate.expressions) != len(members):
            raise AnnotationError(
                f"'{name}' (text_list): {group[0].in_list} also holds non-literal items; "
                "a text_list replaces the whole list"
            )
        wrapped = [i.id for i in group if found[i.id].value_root is not found[i.id].node]
        if wrapped:
            raise AnnotationError(
                f"'{name}' (text_list): {', '.join(wrapped)} sit inside a wrapper "
                f"({found[wrapped[0]].value_root.sql()}); a text_list replaces whole IN "
                "lists, so annotate those literals as separate text variables instead"
            )
    return groups


def _group_places(
    annotation: Annotation,
    infos: list[LiteralInfo],
    analysis: QueryAnalysis,
    found: dict[str, FoundLiteral],
) -> list[list[LiteralInfo]]:
    name, vtype = annotation.name, annotation.type
    if vtype is VariableType.TEXT_LIST:
        return _in_list_places(name, infos, analysis, found)
    if vtype is VariableType.DATE_RANGE:
        if len(infos) % 2:
            raise AnnotationError(
                f"'{name}' (date_range) takes [start, end] pairs, got {len(infos)} literal(s)"
            )
        return [infos[i : i + 2] for i in range(0, len(infos), 2)]
    for info in infos:
        if info.in_list is not None:
            raise AnnotationError(
                f"'{name}': {info.id} is part of IN list {info.in_list}; use a text_list "
                "variable covering the whole list"
            )
    return [[info] for info in infos]


# ── Reading original values ─────────────────────────────────────────────────────────


def _slot_format(annotation: Annotation, info: LiteralInfo) -> str | None:
    """Bare date text is read/written with the analyst's format, else the SQL's suggestion,
    else ISO. Typed literals (`DATE '...'`, CAST) are always ISO."""
    if info.form is LiteralForm.TYPED:
        return None
    return annotation.format or info.suggested_format


def _read_date_literal(name: str, info: LiteralInfo, fmt: str | None) -> dt.date:
    try:
        value = read_date(info.value, fmt)
    except ValueError as exc:
        how = f"with format {fmt!r}" if fmt else "as YYYY-MM-DD (set `format` for other text)"
        raise AnnotationError(
            f"'{name}': literal {info.id} = {info.value!r} can't be read {how}: {exc}. "
            "Date variables replace date literals only (time-of-day values stay fixed logic)."
        ) from exc
    if write_date(value, fmt) != info.value:
        raise AnnotationError(
            f"'{name}': format {fmt!r} reads {info.id} = {info.value!r} but writes it back as "
            f"{write_date(value, fmt)!r}; use a format that reproduces the text exactly"
        )
    return value


def _read_number_literal(name: str, info: LiteralInfo) -> Decimal:
    if info.is_string:
        raise AnnotationError(f"'{name}' (number): {info.id} is a string literal")
    try:
        number = number_from_text(info.value)
    except ValueError as exc:
        raise AnnotationError(f"'{name}' (number): {exc}") from exc
    if number != number.to_integral_value() and Decimal(repr(float(number))) != number:
        raise AnnotationError(
            f"'{name}' (number): {info.value} has more precision than a JSON number carries; "
            "leave it as fixed SQL"
        )
    return number


def _scalar_value(annotation: Annotation, info: LiteralInfo, fmt: str | None) -> Any:
    name, vtype = annotation.name, annotation.type
    match vtype:
        case VariableType.DATE | VariableType.DATE_RANGE:
            return _read_date_literal(name, info, fmt)
        case VariableType.NUMBER:
            return _read_number_literal(name, info)
        case VariableType.TEXT | VariableType.TEXT_LIST:
            if not info.is_string:
                raise AnnotationError(f"'{name}' ({vtype}): {info.id} is not a string literal")
            return info.value
        case VariableType.BOOLEAN:
            if info.value not in ("TRUE", "FALSE"):
                raise AnnotationError(f"'{name}' (boolean): {info.id} is not TRUE/FALSE")
            return info.value == "TRUE"
    raise AnnotationError(f"Unsupported type {vtype}")  # pragma: no cover


def _slot(annotation: Annotation, infos: list[LiteralInfo], field: str, place: int) -> VariableSlot:
    first = infos[0]
    forms = {(i.form, i.cast_type) for i in infos}
    if len(forms) != 1:
        raise AnnotationError(
            f"'{annotation.name}': {first.in_list or first.id} mixes typed and bare "
            "literals; one IN list must be written uniformly"
        )
    typed = first.form is LiteralForm.TYPED
    is_date = annotation.type in _DATE_TYPES
    return VariableSlot(
        placeholder=placeholder_name(annotation.name, field, place),
        field=field,
        literals=[i.id for i in infos],
        literal_form=first.form,
        cast_type=first.cast_type if typed else None,
        format=_slot_format(annotation, first) if is_date else None,
        original_text=first.value if annotation.type is VariableType.NUMBER else None,
    )


def _build_place(annotation: Annotation, infos: list[LiteralInfo], place: int) -> _Place:
    vtype = annotation.type
    if vtype is VariableType.TEXT_LIST:
        slot = _slot(annotation, infos, "", place)
        values = [_scalar_value(annotation, i, None) for i in infos]
        return _Place(infos, [slot], values)
    fields = PLACEHOLDER_FIELDS[vtype]
    slots = [_slot(annotation, [i], f, place) for i, f in zip(infos, fields, strict=True)]
    values = [_scalar_value(annotation, i, s.format) for i, s in zip(infos, slots, strict=True)]
    return _Place(infos, slots, tuple(values) if len(values) > 1 else values[0])


def _plain_original(annotation: Annotation, value: Any) -> Any:
    """Typed place value → validated plain value (the variable's original_value)."""
    vtype = annotation.type
    raw: Any = value
    if vtype is VariableType.DATE_RANGE:
        raw = {"start": value[0].isoformat(), "end": value[1].isoformat()}
    elif vtype is VariableType.DATE:
        raw = value.isoformat()
    try:
        return to_plain(vtype, validate_value(annotation.name, vtype, raw))
    except VariableValueError as exc:
        raise AnnotationError(f"Original value doesn't fit its type: {exc}") from exc


def _same_value(annotation: Annotation, places: list[_Place]) -> None:
    values = [p.value for p in places]
    if any(v != values[0] for v in values[1:]):
        shown = "; ".join(
            f"{[i.id for i in p.infos]} = {[i.value for i in p.infos]}" for p in places
        )
        raise AnnotationError(
            f"'{annotation.name}': every place must hold the same original value, got {shown}"
        )


# ── Facts → issues ──────────────────────────────────────────────────────────────────


def _range_shape(
    annotation: Annotation, places: list[_Place], analysis: QueryAnalysis
) -> RangeShape | None:
    shapes: list[RangeShape | None] = []
    for place in places:
        start, end = (i.id for i in place.infos)
        if any(r.start == end and r.end == start for r in analysis.ranges):
            raise AnnotationError(
                f"'{annotation.name}': {start} and {end} are a range pair in the other "
                f"order; list the lower bound first: [{end}, {start}]"
            )
        match = next((r for r in analysis.ranges if r.start == start and r.end == end), None)
        shapes.append(match.shape if match else None)
    return shapes[0] if shapes and all(s is not None and s == shapes[0] for s in shapes) else None


def _type_issues(annotation: Annotation, infos: list[LiteralInfo], dialect: Dialect) -> list[Issue]:
    """Type compatibility against the compared expression's type (ADR 0015)."""
    issues: list[Issue] = []
    seen: set[tuple[str | None, str | None]] = set()
    for info in infos:
        type_str = info.compared_type or (info.column.type if info.column else None)
        if (info.column_sql, type_str) in seen:
            continue
        seen.add((info.column_sql, type_str))
        fits = compatible_variable_types(type_str, dialect)
        if not fits:
            issues.append(
                _issue(
                    IssueCode.TYPE_UNKNOWN,
                    Severity.WARNING,
                    f"Type of {info.column_sql} is unknown"
                    + (f" ({type_str})" if type_str else "")
                    + "; the variable's type could not be checked against it.",
                    annotation.name,
                    info.id,
                )
            )
        elif annotation.type not in fits:
            issues.append(
                _issue(
                    IssueCode.TYPE_MISMATCH,
                    Severity.ERROR,
                    f"'{annotation.name}' is {annotation.type}, but {info.column_sql} is "
                    f"{type_str} (fits: {', '.join(sorted(fits))}).",
                    annotation.name,
                    info.id,
                )
            )
    return issues


def _ambiguous_format(annotation: Annotation, places: list[_Place]) -> Issue | None:
    fmt = annotation.format
    if fmt is None:
        return None
    for place in places:
        dates = place.value if isinstance(place.value, tuple) else (place.value,)
        for info, slot, value in zip(place.infos, place.slots, dates, strict=True):
            suggested = slot.format == info.suggested_format
            if slot.literal_form is LiteralForm.TYPED or suggested:
                continue
            if value.day <= 12 and value.month <= 12 and value.day != value.month:
                return _issue(
                    IssueCode.AMBIGUOUS_DATE_FORMAT,
                    Severity.WARNING,
                    f"{info.id} = {info.value!r} reads as {value.isoformat()} with {fmt!r}, "
                    "but day and month could be swapped; check the format.",
                    annotation.name,
                    info.id,
                )
    return None


def _description_issue(
    annotation: Annotation, infos: list[LiteralInfo], shape: RangeShape | None
) -> Issue | None:
    if annotation.role is not Role.CHANGEABLE or annotation.description.strip():
        return None
    facts = sorted({c for i in infos for c in i.context if c in _NEEDS_DESCRIPTION})
    reasons = [f"its literals are {', '.join(facts)}"] if facts else []
    if annotation.type is VariableType.DATE_RANGE and shape is None:
        reasons.append("its bounds are not a simple range")
    if not reasons:
        return None
    return _issue(
        IssueCode.DESCRIPTION_REQUIRED,
        Severity.ERROR,
        f"'{annotation.name}' needs a description: {' and '.join(reasons)}, so its name "
        "alone can't say what it does.",
        annotation.name,
    )


# ── Building one variable ───────────────────────────────────────────────────────────


def _column(annotation: Annotation, first: LiteralInfo) -> str | None:
    if annotation.column:
        return annotation.column
    if first.column and first.column.table:
        return f"{first.column.table}.{first.column.column}"
    return first.column_sql


def _variable(
    annotation: Annotation,
    infos: list[LiteralInfo],
    analysis: QueryAnalysis,
    found: dict[str, FoundLiteral],
) -> tuple[VariableSpec, list[Issue]]:
    grouped = _group_places(annotation, infos, analysis, found)
    places = [_build_place(annotation, group, n) for n, group in enumerate(grouped, start=1)]
    is_range = annotation.type is VariableType.DATE_RANGE
    shape = _range_shape(annotation, places, analysis) if is_range else None
    _same_value(annotation, places)
    original = _plain_original(annotation, places[0].value)
    issues = _type_issues(annotation, infos, analysis.dialect)
    if is_range:
        if shape is None:
            issues.append(
                _issue(
                    IssueCode.RANGE_NOT_SIMPLE,
                    Severity.WARNING,
                    f"'{annotation.name}': the bounds are not one suggested range pair (same "
                    "AND chain or BETWEEN, same column), so no shape is recorded; describe "
                    "what start and end mean.",
                    annotation.name,
                )
            )
    extra = [_ambiguous_format(annotation, places), _description_issue(annotation, infos, shape)]
    issues += [i for i in extra if i is not None]
    first = infos[0]
    spec = VariableSpec(
        name=annotation.name,
        type=annotation.type,
        role=annotation.role,
        column=_column(annotation, first),
        column_type=first.compared_type or (first.column.type if first.column else None),
        shape=shape,
        format=annotation.format,
        unit=annotation.unit,
        original_value=original,
        allowed=annotation.allowed,
        description=annotation.description,
        slots=[slot for place in places for slot in place.slots],
    )
    return spec, issues


def _place_placeholders(spec: VariableSpec, found: dict[str, FoundLiteral]) -> None:
    """Swap each slot's literal(s) for its named placeholder in the parsed tree."""
    for slot in spec.slots:
        placeholder = exp.Placeholder(this=slot.placeholder)
        if spec.type is VariableType.TEXT_LIST:
            found[slot.literals[0]].predicate.set("expressions", [placeholder])
        else:
            found[slot.literals[0]].node.replace(placeholder)


def apply_annotations(
    sql: str,
    dialect: Dialect,
    analysis: QueryAnalysis,
    annotations: list[Annotation],
) -> AnnotatedTemplate:
    """Replace annotated literals with placeholders; return template, specs and issues.

    Raises AnnotationError when no template can be built (see module docstring).
    """
    tree = parse(sql, dialect)
    found = {f.id: f for f in find_literals(tree)}
    names = [a.name for a in annotations]
    if len(names) != len(set(names)):
        raise AnnotationError("Variable names must be unique")

    used: set[str] = set()
    specs: list[VariableSpec] = []
    issues: list[Issue] = []
    for annotation in annotations:
        _check_options(annotation)
        infos = _resolve_infos(annotation, analysis, found, used)
        spec, spec_issues = _variable(annotation, infos, analysis, found)
        specs.append(spec)
        issues += spec_issues
    for spec in specs:
        _place_placeholders(spec, found)
    return AnnotatedTemplate(
        template=tree.sql(dialect=str(dialect), pretty=True), variables=specs, issues=issues
    )
