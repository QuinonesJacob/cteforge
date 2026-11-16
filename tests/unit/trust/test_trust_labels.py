import pytest
from pydantic import ValidationError

from cteforge.domain.issues import Issue, IssueCode, Severity
from cteforge.domain.models import DefinitionState, TeamMode, TrustLabel
from cteforge.trust import TrustInputs, TrustResult, assess

V = DefinitionState.VERIFIED
L = DefinitionState.LLM_ANNOTATED
D = DefinitionState.DRAFT

VER = TrustLabel.VERIFIED
BFV = TrustLabel.BUILT_FROM_VERIFIED
LLM = TrustLabel.LLM_ANNOTATED
NEW = TrustLabel.NEW_LOGIC_KNOWN_JOINS
EXP = TrustLabel.EXPLORATORY


def issue(code: IssueCode, variable: str | None = None) -> Issue:
    return Issue(code=code, severity=Severity.WARNING, message=code.value, variable=variable)


OVERRIDE = issue(IssueCode.FIXED_OVERRIDE, "region")
NOT_ALLOWED = issue(IssueCode.VALUE_NOT_ALLOWED, "segment")
TRANSPILED = issue(IssueCode.TRANSPILED)
UNKNOWN_JOIN = issue(IssueCode.UNKNOWN_JOIN)
FAN_OUT = issue(IssueCode.FAN_OUT_RISK)


# (id, inputs kwargs, expected label)
LABEL_CASES = [
    # Base cases
    ("one_verified", dict(definition_states=[V]), VER),
    ("many_verified_no_select", dict(definition_states=[V, V]), VER),
    ("select_over_verified", dict(definition_states=[V, V], has_select=True), BFV),
    ("one_llm", dict(definition_states=[L]), LLM),
    ("llm_and_verified", dict(definition_states=[V, L]), LLM),
    ("select_over_llm", dict(definition_states=[V, L], has_select=True), LLM),
    ("raw_known_joins", dict(definition_states=[], raw_sql=True), NEW),
    ("raw_unknown_joins", dict(definition_states=[], raw_sql=True, joins_known=False), EXP),
    ("no_definitions_not_raw", dict(definition_states=[]), NEW),
    # Unknown joins
    (
        "select_unknown_join_flag",
        dict(definition_states=[V], has_select=True, joins_known=False),
        EXP,
    ),
    (
        "select_unknown_join_issue",
        dict(definition_states=[V], has_select=True, issues=[UNKNOWN_JOIN]),
        EXP,
    ),
    ("unknown_join_issue_no_select", dict(definition_states=[V], issues=[UNKNOWN_JOIN]), EXP),
    ("joins_unknown_without_select_ignored", dict(definition_states=[V], joins_known=False), VER),
    (
        "llm_select_unknown_join",
        dict(definition_states=[L], has_select=True, joins_known=False),
        EXP,
    ),
    # Single downgrades
    ("verified_override", dict(definition_states=[V], issues=[OVERRIDE]), BFV),
    ("verified_not_allowed", dict(definition_states=[V], issues=[NOT_ALLOWED]), BFV),
    ("verified_transpiled", dict(definition_states=[V], issues=[TRANSPILED]), BFV),
    ("select_override", dict(definition_states=[V], has_select=True, issues=[OVERRIDE]), LLM),
    ("llm_transpiled", dict(definition_states=[L], issues=[TRANSPILED]), NEW),
    ("raw_known_transpiled", dict(definition_states=[], raw_sql=True, issues=[TRANSPILED]), EXP),
    # Stacked downgrades
    ("verified_two_downgrades", dict(definition_states=[V], issues=[OVERRIDE, TRANSPILED]), LLM),
    (
        "verified_three_downgrades",
        dict(definition_states=[V], issues=[OVERRIDE, NOT_ALLOWED, TRANSPILED]),
        NEW,
    ),
    (
        "verified_four_downgrades",
        dict(
            definition_states=[V],
            issues=[OVERRIDE, NOT_ALLOWED, TRANSPILED, issue(IssueCode.FIXED_OVERRIDE, "x")],
        ),
        EXP,
    ),
    (
        "floor_exploratory",
        dict(
            definition_states=[L],
            issues=[OVERRIDE, NOT_ALLOWED, TRANSPILED, issue(IssueCode.FIXED_OVERRIDE, "x")],
        ),
        EXP,
    ),
    (
        "two_overrides_two_vars",
        dict(definition_states=[V], issues=[OVERRIDE, issue(IssueCode.FIXED_OVERRIDE, "y")]),
        LLM,
    ),
    ("duplicate_issue_counted_once", dict(definition_states=[V], issues=[OVERRIDE, OVERRIDE]), BFV),
    (
        "unknown_join_then_downgrade_floor",
        dict(definition_states=[V], issues=[UNKNOWN_JOIN, OVERRIDE]),
        EXP,
    ),
    ("non_downgrade_issue_ignored", dict(definition_states=[V], issues=[FAN_OUT]), VER),
    # Drafts
    ("draft_alone", dict(definition_states=[D]), EXP),
    ("draft_with_verified", dict(definition_states=[V, D]), EXP),
]


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [(k, e) for _, k, e in LABEL_CASES],
    ids=[i for i, _, _ in LABEL_CASES],
)
def test_label_rules(kwargs, expected):
    result = assess(TrustInputs(**kwargs))
    assert result.label is expected
    assert result.reasons  # every result explains itself


# (id, inputs kwargs, usable, blocking codes)
USABLE_CASES = [
    ("open_verified", dict(definition_states=[V]), True, []),
    ("open_llm", dict(definition_states=[L]), True, []),
    ("open_raw_exploratory", dict(definition_states=[], raw_sql=True, joins_known=False), True, []),
    ("open_draft", dict(definition_states=[D]), False, [IssueCode.DRAFT_NOT_USABLE]),
    (
        "open_draft_among_verified",
        dict(definition_states=[V, V, D]),
        False,
        [IssueCode.DRAFT_NOT_USABLE],
    ),
    ("strict_verified", dict(definition_states=[V], mode=TeamMode.STRICT), True, []),
    (
        "strict_built_from_verified",
        dict(definition_states=[V, V], has_select=True, mode=TeamMode.STRICT),
        True,
        [],
    ),
    (
        "strict_verified_downgraded_once",
        dict(definition_states=[V], issues=[OVERRIDE], mode=TeamMode.STRICT),
        False,
        [IssueCode.STRICT_MODE_BLOCKED],
    ),
    (
        "strict_verified_transpiled",
        dict(definition_states=[V], issues=[TRANSPILED], mode=TeamMode.STRICT),
        False,
        [IssueCode.STRICT_MODE_BLOCKED],
    ),
    (
        "strict_verified_value_not_allowed",
        dict(definition_states=[V, V], issues=[NOT_ALLOWED], mode=TeamMode.STRICT),
        False,
        [IssueCode.STRICT_MODE_BLOCKED],
    ),
    (
        "strict_verified_non_downgrade_warning_ok",
        dict(definition_states=[V, V], has_select=True, issues=[FAN_OUT], mode=TeamMode.STRICT),
        True,
        [],
    ),
    (
        "open_verified_downgraded_still_usable",
        dict(definition_states=[V], issues=[OVERRIDE, TRANSPILED]),
        True,
        [],
    ),
    (
        "strict_llm",
        dict(definition_states=[L], mode=TeamMode.STRICT),
        False,
        [IssueCode.STRICT_MODE_BLOCKED],
    ),
    (
        "strict_select_downgraded",
        dict(definition_states=[V], has_select=True, issues=[OVERRIDE], mode=TeamMode.STRICT),
        False,
        [IssueCode.STRICT_MODE_BLOCKED],
    ),
    (
        "strict_raw_sql",
        dict(definition_states=[], raw_sql=True, mode=TeamMode.STRICT),
        False,
        [IssueCode.STRICT_MODE_BLOCKED],
    ),
    (
        "strict_unknown_join",
        dict(definition_states=[V], has_select=True, joins_known=False, mode=TeamMode.STRICT),
        False,
        [IssueCode.STRICT_MODE_BLOCKED],
    ),
    (
        "strict_draft_blocks_twice",
        dict(definition_states=[D], mode=TeamMode.STRICT),
        False,
        [IssueCode.DRAFT_NOT_USABLE, IssueCode.STRICT_MODE_BLOCKED],
    ),
]


@pytest.mark.parametrize(
    ("kwargs", "usable", "codes"),
    [(k, u, c) for _, k, u, c in USABLE_CASES],
    ids=[i for i, *_ in USABLE_CASES],
)
def test_usable_and_blocking(kwargs, usable, codes):
    result = assess(TrustInputs(**kwargs))
    assert result.usable is usable
    assert [b.code for b in result.blocking] == codes
    assert all(b.severity is Severity.ERROR and b.message for b in result.blocking)


def test_reasons_name_each_rule_that_applied():
    result = assess(
        TrustInputs(
            definition_states=[V, L],
            has_select=True,
            issues=[OVERRIDE, TRANSPILED],
        )
    )
    text = " ".join(result.reasons).lower()
    assert "select" in text
    assert "reviewed" in text  # llm_annotated cap
    assert "fixed variable was overridden" in text
    assert "transpiled" in text
    assert len(result.reasons) == 4
    assert result.label is EXP  # capped at llm_annotated, then two steps down


def test_downgrade_at_floor_is_still_explained():
    result = assess(
        TrustInputs(definition_states=[], raw_sql=True, joins_known=False, issues=[TRANSPILED])
    )
    assert result.label is EXP
    assert any("lowest" in r for r in result.reasons)


def test_strict_message_names_the_label():
    result = assess(TrustInputs(definition_states=[L], mode=TeamMode.STRICT))
    assert "llm_annotated" in result.blocking[0].message


def test_models_are_frozen():
    inputs = TrustInputs(definition_states=[V])
    with pytest.raises(ValidationError):
        inputs.raw_sql = True  # type: ignore[misc]
    result = assess(inputs)
    assert isinstance(result, TrustResult)
    with pytest.raises(ValidationError):
        result.usable = False  # type: ignore[misc]


def test_inputs_reject_unknown_fields():
    with pytest.raises(ValidationError):
        TrustInputs(definition_states=[V], surprise=True)  # type: ignore[call-arg]


def test_default_issues_not_shared():
    a = TrustInputs(definition_states=[V])
    b = TrustInputs(definition_states=[V])
    assert a.issues == [] and a.issues is not b.issues


def test_strict_downgrade_block_keeps_label_and_names_cause():
    # Regression: a one-step downgrade of a verified result lands on
    # built_from_verified_parts; strict mode must still block it, and the label is unchanged.
    result = assess(TrustInputs(definition_states=[V], issues=[OVERRIDE], mode=TeamMode.STRICT))
    assert result.label is BFV
    assert result.usable is False
    assert [b.code for b in result.blocking] == [IssueCode.STRICT_MODE_BLOCKED]
    assert "fixed variable was overridden" in result.blocking[0].message
