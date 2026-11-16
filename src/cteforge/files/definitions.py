"""Read/write definition and annotation files. Format lives in domain/codec.py."""

from __future__ import annotations

from pathlib import Path

from cteforge.domain.codec import (
    AnnotationFile,
    annotations_from_yaml,
    definition_from_yaml,
    definition_to_yaml,
)
from cteforge.domain.errors import CodecError
from cteforge.domain.models import Definition


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CodecError(f"Cannot read {path}: {exc.strerror}") from exc


def load_definition(path: str | Path) -> Definition:
    return definition_from_yaml(_read(Path(path)))


def save_definition(path: str | Path, definition: Definition) -> None:
    Path(path).write_text(definition_to_yaml(definition), encoding="utf-8")


def load_annotations(path: str | Path) -> AnnotationFile:
    return annotations_from_yaml(_read(Path(path)))


def load_sql(path: str | Path) -> str:
    return _read(Path(path))
