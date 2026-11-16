import pytest
from pydantic import ValidationError

from cteforge.domain.codec import definition_from_yaml, definition_to_yaml
from cteforge.domain.errors import CodecError
from cteforge.domain.models import Definition


def test_definition_yaml_roundtrip(sample_definition):
    text = definition_to_yaml(sample_definition)
    assert definition_from_yaml(text) == sample_definition
    assert "template: |" in text  # multi-line SQL stays readable


def test_rejects_unknown_schema_version(sample_definition):
    text = definition_to_yaml(sample_definition).replace("schema_version: 1", "schema_version: 99")
    with pytest.raises(CodecError, match="schema_version 99"):
        definition_from_yaml(text)


def test_rejects_invalid_ids():
    with pytest.raises(CodecError, match="lower_snake_case"):
        definition_from_yaml("id: Bad-Id\ntitle: x\nsource_dialect: duckdb\ntemplate: SELECT 1\n")


def test_unknown_fields_are_rejected():
    with pytest.raises(CodecError):
        definition_from_yaml(
            "id: x\ntitle: x\nsource_dialect: duckdb\ntemplate: SELECT 1\nsurprise: 1\n"
        )


def test_model_is_frozen(sample_definition: Definition):
    with pytest.raises(ValidationError):
        sample_definition.title = "changed"  # type: ignore[misc]
