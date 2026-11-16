"""Read/write a library directory: the optional git export of the store (ADR 0003).

Layout (one YAML file per object, named by its id; deterministic output for clean diffs):
    <dir>/definitions/<id>.yml   definition codec YAML (domain/codec.py), latest version
    <dir>/recipes/<id>.yml       Recipe fields, in model field order
    <dir>/joins/<id>.yml         JoinEdge fields, in model field order

Recipes and joins have no codec format of their own; they are written as their model's
plain fields and read back through model validation. Malformed files raise CodecError
naming the file, never echoing its contents.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cteforge.domain.codec import definition_from_yaml, definition_to_yaml
from cteforge.domain.errors import CodecError
from cteforge.domain.models import Definition, JoinEdge, Recipe

DEFINITIONS_DIR = "definitions"
RECIPES_DIR = "recipes"
JOINS_DIR = "joins"
SUFFIX = ".yml"
_SAFE_NAME = re.compile(r"^[a-z0-9_][a-z0-9_.=,-]*$")


class LibraryFiles(BaseModel):
    """Everything read from a library directory, each list sorted by file name."""

    model_config = ConfigDict(frozen=True)

    definitions: list[Definition] = Field(default_factory=list)
    recipes: list[Recipe] = Field(default_factory=list)
    joins: list[JoinEdge] = Field(default_factory=list)


def _plain(value: Any) -> Any:
    """Model dump → YAML-safe plain data (Decimal → int/float, containers recursively)."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _dump(model: BaseModel) -> str:
    data = model.model_dump(mode="json")
    if isinstance(model, Recipe):  # keep exact numbers in values (mode="json" would str them)
        data["values"] = _plain(model.values)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def _file_name(id: str) -> str:
    if not _SAFE_NAME.match(id):
        raise CodecError(f"Cannot export '{id}': its id is not a safe file name")
    return id + SUFFIX


def _write_all(folder: Path, texts: dict[str, str], prune: bool) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, text in sorted(texts.items()):
        path = folder / name
        path.write_text(text, encoding="utf-8")
        written.append(path)
    if prune:
        for stale in folder.glob("*" + SUFFIX):
            if stale.name not in texts:
                stale.unlink()
    return written


def write_library(
    directory: str | Path,
    *,
    definitions: list[Definition],
    recipes: list[Recipe],
    joins: list[JoinEdge],
    prune: bool = False,
) -> list[Path]:
    """Write every object as its own file. `prune=True` deletes other `*.yml` files in the
    three sub-directories, so the directory mirrors the library exactly."""
    root = Path(directory)
    try:
        written = _write_all(
            root / DEFINITIONS_DIR,
            {_file_name(d.id): definition_to_yaml(d) for d in definitions},
            prune,
        )
        written += _write_all(
            root / RECIPES_DIR, {_file_name(r.id): _dump(r) for r in recipes}, prune
        )
        written += _write_all(root / JOINS_DIR, {_file_name(j.id): _dump(j) for j in joins}, prune)
    except OSError as exc:
        raise CodecError(f"Cannot write library to {root}: {exc.strerror}") from exc
    return written


def _texts(folder: Path) -> list[tuple[Path, str]]:
    if not folder.is_dir():
        return []
    out: list[tuple[Path, str]] = []
    for path in sorted(folder.glob("*" + SUFFIX)):
        try:
            out.append((path, path.read_text(encoding="utf-8")))
        except OSError as exc:
            raise CodecError(f"Cannot read {path}: {exc.strerror}") from exc
    return out


def _model(path: Path, text: str, model: type[BaseModel]) -> Any:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        raise CodecError(f"{path.name}: invalid YAML") from None
    if not isinstance(data, dict):
        raise CodecError(f"{path.name}: expected a YAML mapping at the top level")
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        where = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})
        raise CodecError(f"{path.name}: invalid fields {where}") from None


def read_library(directory: str | Path) -> LibraryFiles:
    """Read a library directory written by `write_library` (missing sub-directories are
    empty). Raises CodecError when the directory is missing or a file is malformed."""
    root = Path(directory)
    if not root.is_dir():
        raise CodecError(f"Library directory not found: {root}")
    definitions: list[Definition] = []
    for path, text in _texts(root / DEFINITIONS_DIR):
        try:
            definitions.append(definition_from_yaml(text))
        except CodecError as exc:
            raise CodecError(f"{path.name}: {exc}") from None
    return LibraryFiles(
        definitions=definitions,
        recipes=[_model(p, t, Recipe) for p, t in _texts(root / RECIPES_DIR)],
        joins=[_model(p, t, JoinEdge) for p, t in _texts(root / JOINS_DIR)],
    )
