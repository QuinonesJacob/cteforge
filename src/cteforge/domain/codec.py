"""Definition / annotation file format: YAML text ⇄ models. No file I/O here (files/ does it).

Definition files carry `schema_version`; bump SCHEMA_VERSION and add a migration here
when the format changes.

Legacy layout (M1, still schema_version 1): a variable carried `literal_form` / `cast_type`
itself and filled exactly one place. It is read by converting it to one place of `slots`
(`_legacy_slots`); files are always written in the current layout.
"""

from __future__ import annotations

from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cteforge.domain.errors import CodecError
from cteforge.domain.models import SCHEMA_VERSION, Annotation, Definition
from cteforge.domain.variables import PLACEHOLDER_FIELDS, VariableType, placeholder_name


class AnnotationFile(BaseModel):
    """Input to `annotate`: metadata about a trusted query + its variable annotations."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    title: str
    meaning: str = ""
    keywords: list[str] = Field(default_factory=list)
    use_when: str = ""
    dont_use_when: str = ""
    grain: str | None = None
    variables: list[Annotation] = Field(default_factory=list)


def _load_yaml(text: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CodecError(f"Invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise CodecError("Expected a YAML mapping at the top level")
    return data


def _describe(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())


def _legacy_slots(variable: Any) -> Any:
    """M1 variable (literal_form/cast_type, one place) → current layout with `slots`."""
    if not isinstance(variable, dict) or "slots" in variable:
        return variable
    try:
        fields = PLACEHOLDER_FIELDS[VariableType(variable.get("type"))]
    except ValueError:
        return variable  # Let validation report the bad type.
    migrated = {k: v for k, v in variable.items() if k not in ("literal_form", "cast_type")}
    migrated["slots"] = [
        {
            "placeholder": placeholder_name(str(variable.get("name")), field),
            "field": field,
            "literal_form": variable.get("literal_form", "bare"),
            "cast_type": variable.get("cast_type"),
        }
        for field in fields
    ]
    return migrated


def definition_from_yaml(text: str) -> Definition:
    data = _load_yaml(text)
    version = data.get("schema_version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise CodecError(
            f"Unsupported definition schema_version {version} (expected {SCHEMA_VERSION})"
        )
    if isinstance(data.get("variables"), list):
        data = {**data, "variables": [_legacy_slots(v) for v in data["variables"]]}
    try:
        return Definition.model_validate(data)
    except ValidationError as exc:
        raise CodecError(f"Invalid definition: {_describe(exc)}") from exc


class _LiteralBlockDumper(yaml.SafeDumper):
    """Write multi-line strings (SQL templates) as readable `|` blocks."""


def _str_representer(dumper: yaml.SafeDumper, value: str) -> yaml.Node:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_LiteralBlockDumper.add_representer(str, _str_representer)


def definition_to_yaml(definition: Definition) -> str:
    data = definition.model_dump(mode="json", exclude_none=True)
    return yaml.dump(data, Dumper=_LiteralBlockDumper, sort_keys=False, allow_unicode=True)


def annotations_from_yaml(text: str) -> AnnotationFile:
    try:
        return AnnotationFile.model_validate(_load_yaml(text))
    except ValidationError as exc:
        raise CodecError(f"Invalid annotation file: {_describe(exc)}") from exc
