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


LEGACY_M1 = """\
schema_version: 1
id: legacy
title: Legacy
source_dialect: duckdb
template: SELECT * FROM t WHERE d >= $period__start AND d < $period__end AND s IN ($codes)
variables:
- name: period
  type: date_range
  role: changeable
  literal_form: typed
  cast_type: TIMESTAMP
  shape: half_open
  original_value: {start: '2026-01-01', end: '2026-04-01'}
- name: codes
  type: text_list
  role: fixed
  literal_form: bare
  original_value: [a, b]
"""


def test_legacy_m1_variables_are_read_as_one_place():
    definition = definition_from_yaml(LEGACY_M1)
    period = definition.variable("period")
    assert [(s.placeholder, s.field, s.literal_form, s.cast_type) for s in period.slots] == [
        ("period__start", "start", "typed", "TIMESTAMP"),
        ("period__end", "end", "typed", "TIMESTAMP"),
    ]
    assert [s.placeholder for s in definition.variable("codes").slots] == ["codes"]
    written = definition_to_yaml(definition)
    assert "literal_form: typed" in written and "slots:" in written
    assert definition_from_yaml(written) == definition


def test_multi_place_definition_roundtrips(collections_definition):
    text = definition_to_yaml(collections_definition)
    assert definition_from_yaml(text) == collections_definition


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"type": "text", "format": "%Y"}, "format applies to date"),
        ({"type": "text", "unit": "USD"}, "unit applies to number only"),
        (
            {"type": "date_range", "original_value": {"start": "2026-01-01", "end": "2026-02-01"}},
            "repeat the fields",
        ),
    ],
)
def test_variable_spec_consistency(patch, message):
    import yaml

    data = yaml.safe_load(LEGACY_M1)
    variable = {
        "name": "v",
        "role": "fixed",
        "original_value": "a",
        "slots": [{"placeholder": "v"}],
        **patch,
    }
    data["variables"] = [variable]
    with pytest.raises(CodecError, match=message):
        definition_from_yaml(yaml.safe_dump(data))
