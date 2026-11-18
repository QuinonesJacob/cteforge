"""Definition / annotation file format: YAML text ⇄ models. No file I/O here (files/ does it).

Definition files carry `schema_version`; bump SCHEMA_VERSION and add a migration here
when the format changes.

Schema 2 (ADR 0026): a definition may carry `composition` (parts by alias + fixed values;
its template is then the final SELECT over the aliases). Version-1 files (SQL only) load
unchanged; files are always written as the current version. Recipes (removed by ADR 0026)
are read as composed definitions: `definition_from_recipe`.

Legacy layout (M1, still schema_version 1): a variable carried `literal_form` / `cast_type`
itself and filled exactly one place. It is read by converting it to one place of `slots`
(`_legacy_slots`); files are always written in the current layout.
"""

from __future__ import annotations

from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cteforge.domain.errors import CodecError
from cteforge.domain.models import SCHEMA_VERSION, Annotation, Definition, DefinitionState
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
    return definition_from_dict(data)


SUPPORTED_SCHEMA_VERSIONS = (1, SCHEMA_VERSION)


def definition_from_dict(data: dict[str, Any]) -> Definition:
    """A definition from its file mapping (any supported schema_version → the current)."""
    version = data.get("schema_version", SCHEMA_VERSION)
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise CodecError(
            f"Unsupported definition schema_version {version} (expected one of "
            f"{list(SUPPORTED_SCHEMA_VERSIONS)})"
        )
    if version == 1 and "composition" in data:
        raise CodecError("schema_version 1 definitions cannot carry a composition")
    data = {**data, "schema_version": SCHEMA_VERSION}
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


# Fields of a (pre-ADR 0026) recipe file / stored recipe.
_RECIPE_FIELDS = {
    "schema_version",
    "id",
    "version",
    "title",
    "description",
    "uses",
    "values",
    "select",
    "dialect",
    "state",
}


def is_recipe_data(data: Any) -> bool:
    """A mapping in the removed recipe format (ADR 0026): `uses` + `dialect`, no template."""
    return (
        isinstance(data, dict) and "uses" in data and "dialect" in data and "template" not in data
    )


def definition_from_recipe(
    data: dict[str, Any], *, id: str | None = None, verified_by: str | None = None
) -> Definition:
    """A recipe (removed by ADR 0026) as the composed definition that replaces it: its
    uses are the parts, its values are `fixed`, its final SELECT the template (none = its
    single part as is), its description the meaning. State: llm_annotated, or verified
    when it was verified AND `verified_by` names who did (else llm_annotated); a draft
    recipe stays a draft. Version 1. `id` overrides the recipe's id (e.g. when taken)."""
    unknown = sorted(set(data) - _RECIPE_FIELDS)
    if unknown:
        raise CodecError(f"Invalid recipe: unknown fields {unknown}")
    state = str(data.get("state") or DefinitionState.LLM_ANNOTATED.value)
    if state == DefinitionState.VERIFIED.value and verified_by is None:
        state = DefinitionState.LLM_ANNOTATED.value
    mapped: dict[str, Any] = {
        "id": id or data.get("id"),
        "version": 1,
        "title": data.get("title") or data.get("id"),
        "meaning": data.get("description") or "",
        "source_dialect": data.get("dialect"),
        "template": data.get("select") or "",
        "composition": {"uses": data.get("uses"), "fixed": data.get("values") or {}},
        "state": state,
        "verified_by": verified_by if state == DefinitionState.VERIFIED.value else None,
    }
    try:
        return Definition.model_validate(mapped)
    except ValidationError as exc:
        raise CodecError(f"Invalid recipe: {_describe(exc)}") from exc


def annotations_from_yaml(text: str) -> AnnotationFile:
    try:
        return AnnotationFile.model_validate(_load_yaml(text))
    except ValidationError as exc:
        raise CodecError(f"Invalid annotation file: {_describe(exc)}") from exc
