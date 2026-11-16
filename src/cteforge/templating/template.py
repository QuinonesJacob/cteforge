"""Annotations + trusted SQL → template text with named placeholders (ADR 0011)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlglot import exp

from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import AnnotationError, VariableValueError
from cteforge.domain.models import Annotation, LiteralForm, VariableSpec
from cteforge.domain.variables import (
    PLACEHOLDER_FIELDS,
    RangeShape,
    VariableType,
    placeholder_name,
    to_plain,
    validate_value,
)
from cteforge.parsing.analyze import LiteralInfo, QueryAnalysis, find_literals, parse
from cteforge.parsing.types import compatible_variable_types


class AnnotatedTemplate(BaseModel):
    model_config = ConfigDict(frozen=True)

    template: str
    variables: list[VariableSpec]


def _single(annotation: Annotation) -> str:
    if len(annotation.literals) != 1:
        raise AnnotationError(
            f"'{annotation.name}' ({annotation.type}) takes exactly one literal, "
            f"got {len(annotation.literals)}"
        )
    return annotation.literals[0]


def _date_text(info: LiteralInfo, name: str) -> str:
    if len(info.value) == 10:
        try:
            dt.date.fromisoformat(info.value)
            return info.value
        except ValueError:
            pass
    raise AnnotationError(
        f"'{name}': literal {info.id} = {info.value!r} is not a plain YYYY-MM-DD date. "
        "Date variables only replace date literals (time-of-day values stay fixed logic)."
    )


def _original_value(
    annotation: Annotation, infos: list[LiteralInfo], analysis: QueryAnalysis
) -> tuple[Any, RangeShape | None]:
    name, vtype = annotation.name, annotation.type
    if vtype is VariableType.DATE_RANGE:
        if len(infos) != 2:
            raise AnnotationError(f"'{name}' (date_range) takes [start_id, end_id]")
        start, end = infos
        match = next(
            (r for r in analysis.ranges if r.start == start.id and r.end == end.id),
            None,
        )
        if match is None:
            raise AnnotationError(
                f"'{name}': {start.id} and {end.id} are not a lower/upper bound pair on "
                "the same column. Detected ranges: "
                + (", ".join(f"[{r.start}, {r.end}]" for r in analysis.ranges) or "none")
            )
        value = {"start": _date_text(start, name), "end": _date_text(end, name)}
        return value, match.shape
    if vtype is VariableType.TEXT_LIST:
        groups = {i.in_list for i in infos}
        if len(groups) != 1 or None in groups:
            raise AnnotationError(f"'{name}' (text_list) must use literals of one IN list")
        group = groups.pop()
        members = [lit.id for lit in analysis.literals if lit.in_list == group]
        if [i.id for i in infos] != members:
            raise AnnotationError(
                f"'{name}' (text_list) must list every literal of {group} in order: {members}"
            )
        if not all(i.is_string for i in infos):
            raise AnnotationError(f"'{name}' (text_list) literals must all be strings")
        return [i.value for i in infos], None
    (info,) = infos
    if info.in_list is not None:
        raise AnnotationError(
            f"'{name}': {info.id} is part of IN list {info.in_list}; use a text_list "
            "variable covering the whole list"
        )
    if vtype is VariableType.DATE:
        return _date_text(info, name), None
    if vtype is VariableType.TEXT:
        if not info.is_string:
            raise AnnotationError(f"'{name}' (text): {info.id} is not a string literal")
        return info.value, None
    if vtype is VariableType.NUMBER:
        if info.is_string:
            raise AnnotationError(f"'{name}' (number): {info.id} is a string literal")
        number = float(info.value)
        return (int(number) if number.is_integer() and "." not in info.value else number), None
    if vtype is VariableType.BOOLEAN:
        if info.value not in ("TRUE", "FALSE"):
            raise AnnotationError(f"'{name}' (boolean): {info.id} is not TRUE/FALSE")
        return info.value == "TRUE", None
    raise AnnotationError(f"Unsupported type {vtype}")  # pragma: no cover


def apply_annotations(
    sql: str,
    dialect: Dialect,
    analysis: QueryAnalysis,
    annotations: list[Annotation],
) -> AnnotatedTemplate:
    """Replace annotated literals with placeholders; return template + variable specs.

    Raises AnnotationError for unknown/reused literal IDs, wrong shapes, or a type that
    doesn't fit the column's real warehouse type.
    """
    tree = parse(sql, dialect)
    found = {f.id: f for f in find_literals(tree)}
    names = [a.name for a in annotations]
    if len(names) != len(set(names)):
        raise AnnotationError("Variable names must be unique")

    used: set[str] = set()
    specs: list[VariableSpec] = []
    replacements: list[tuple[exp.Expression, str, bool]] = []  # node, placeholder, is_list

    for annotation in annotations:
        if annotation.type not in (VariableType.TEXT_LIST, VariableType.DATE_RANGE):
            _single(annotation)
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

        forms = {(i.form, i.cast_type) for i in infos}
        if len(forms) != 1:
            raise AnnotationError(
                f"'{annotation.name}': literals mix typed and bare forms; can't template"
            )
        form, cast_type = forms.pop()

        original, shape = _original_value(annotation, infos, analysis)
        try:
            validate_value(annotation.name, annotation.type, original)
        except VariableValueError as exc:
            raise AnnotationError(f"Original value doesn't fit its type: {exc}") from exc

        first = infos[0]
        column_type = first.column.type if first.column else None
        if column_type is not None:
            allowed_types = compatible_variable_types(column_type, dialect)
            if allowed_types and annotation.type not in allowed_types:
                raise AnnotationError(
                    f"'{annotation.name}' is {annotation.type}, but {first.column_sql} is "
                    f"{column_type} in the warehouse (fits: "
                    f"{', '.join(sorted(allowed_types))})"
                )
        column = annotation.column or (
            f"{first.column.table}.{first.column.column}"
            if first.column and first.column.table
            else first.column_sql
        )
        specs.append(
            VariableSpec(
                name=annotation.name,
                type=annotation.type,
                role=annotation.role,
                column=column,
                column_type=column_type,
                literal_form=form,
                cast_type=cast_type if form is LiteralForm.TYPED else None,
                shape=shape,
                original_value=to_plain(
                    annotation.type,
                    validate_value(annotation.name, annotation.type, original),
                ),
                allowed=annotation.allowed,
                description=annotation.description,
            )
        )

        if annotation.type is VariableType.TEXT_LIST:
            replacements.append(
                (found[infos[0].id].predicate, placeholder_name(annotation.name), True)
            )
        else:
            for info, field in zip(infos, PLACEHOLDER_FIELDS[annotation.type], strict=True):
                replacements.append(
                    (
                        found[info.id].node,
                        placeholder_name(annotation.name, field),
                        False,
                    )
                )

    for node, placeholder, is_list in replacements:
        ph = exp.Placeholder(this=placeholder)
        if is_list:
            node.set("expressions", [ph])
        else:
            node.replace(ph)

    return AnnotatedTemplate(template=tree.sql(dialect=str(dialect), pretty=True), variables=specs)
