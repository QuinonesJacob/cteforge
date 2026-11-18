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
    text = definition_to_yaml(sample_definition).replace("schema_version: 2", "schema_version: 99")
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


# ── ADR 0026: composed definitions (schema 2) and recipe files ──────────────────────

COMPOSED = """\
schema_version: 2
id: rr_emea
title: Realization EMEA
source_dialect: duckdb
template: SELECT rr.month FROM rr
composition:
  uses:
    rr:
      id: realization_rate
    bc:
      id: booked_vs_collected
      version: 2
  fixed:
    rr:
      regions: [EMEA]
"""


def test_composed_definition_round_trips():
    from cteforge.domain.models import DefinitionRef

    d = definition_from_yaml(COMPOSED)
    assert d.composed and d.composition is not None
    assert d.composition.uses["bc"] == DefinitionRef(id="booked_vs_collected", version=2)
    assert d.composition.uses["rr"].version is None  # floating
    assert d.composition.fixed == {"rr": {"regions": ["EMEA"]}}
    assert d.uses_definition("booked_vs_collected") and not d.uses_definition("x")
    assert definition_from_yaml(definition_to_yaml(d)) == d


def test_version_1_files_still_load_as_the_current_version(sample_definition):
    text = definition_to_yaml(sample_definition).replace("schema_version: 2", "schema_version: 1")
    loaded = definition_from_yaml(text)
    assert loaded.schema_version == 2 and loaded == sample_definition
    with pytest.raises(CodecError, match="cannot carry a composition"):
        definition_from_yaml(COMPOSED.replace("schema_version: 2", "schema_version: 1"))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (("    rr:\n      regions", "    xx:\n      regions"), "not in uses"),
        (("template: SELECT rr.month FROM rr", "template: ''"), "exactly one part"),
        (("    rr:\n      id", "    Bad-Alias:\n      id"), "lower_snake_case"),
        (("composition:", "composition_x:"), "composition_x"),
    ],
)
def test_invalid_compositions_are_refused(change, message):
    with pytest.raises(CodecError, match=message):
        definition_from_yaml(COMPOSED.replace(*change))


def test_sql_definition_needs_a_template():
    with pytest.raises(CodecError, match="SQL"):
        definition_from_yaml("id: x\ntitle: x\nsource_dialect: duckdb\ntemplate: ''\n")


def test_rename_rewrites_parts_and_keeps_pins():
    d = definition_from_yaml(COMPOSED)
    renamed = d.with_definition_renamed("booked_vs_collected", "booked")
    assert (
        renamed.composition.uses["bc"].id == "booked"
        and renamed.composition.uses["bc"].version == 2
    )
    assert d.with_definition_renamed("unused", "x") is d


RECIPE = {
    "schema_version": 1,
    "id": "apac_q3",
    "version": 4,
    "title": "APAC Q3",
    "description": "APAC only",
    "uses": {"rr": {"id": "realization_rate"}},
    "values": {"rr": {"regions": ["APAC"]}},
    "select": None,
    "dialect": "duckdb",
    "state": "verified",
}


def test_recipe_becomes_a_composed_definition():
    from cteforge.domain.codec import definition_from_recipe, is_recipe_data
    from cteforge.domain.models import DefinitionState

    assert is_recipe_data(RECIPE) and not is_recipe_data({"template": "x"})
    d = definition_from_recipe(RECIPE)
    assert (d.id, d.version, d.meaning, d.template) == ("apac_q3", 1, "APAC only", "")
    assert d.composition.fixed == {"rr": {"regions": ["APAC"]}}
    # Verified only when the verifier is known.
    assert d.state is DefinitionState.LLM_ANNOTATED and d.verified_by is None
    verified = definition_from_recipe(RECIPE, id="apac_q3_recipe", verified_by="ann")
    assert verified.id == "apac_q3_recipe" and verified.state is DefinitionState.VERIFIED
    assert verified.verified_by == "ann"
    draft = definition_from_recipe(RECIPE | {"state": "draft"}, verified_by="ann")
    assert draft.state is DefinitionState.DRAFT and draft.verified_by is None
    with pytest.raises(CodecError, match="unknown fields"):
        definition_from_recipe(RECIPE | {"surprise": 1})
    with pytest.raises(CodecError, match="exactly one part"):
        definition_from_recipe(
            RECIPE | {"uses": {"a": {"id": "x"}, "b": {"id": "y"}}, "values": {}}
        )
