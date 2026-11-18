"""Result trust labels (DESIGN §8, ADR 0007). Pure: no I/O.

`assess` turns what went into a result (definition states, agent-written SQL, render/compose
issues, team mode) into one label plus the plain-language reasons behind it.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from cteforge.domain.issues import Issue, IssueCode, Severity
from cteforge.domain.models import TRUST_ORDER, DefinitionState, TeamMode, TrustLabel

# Issues that each lower the label one step along TRUST_ORDER.
DOWNGRADE_CODES: dict[IssueCode, str] = {
    IssueCode.FIXED_OVERRIDE: "a fixed variable was overridden",
    IssueCode.VALUE_NOT_ALLOWED: "a value outside the variable's allowed list was used",
    IssueCode.TRANSPILED: "the SQL was transpiled to another dialect",
}

# Labels usable in strict mode (DESIGN §8: strict = only verified usable). A result that
# reached one of these only after a downgrade (override, out-of-list value, transpile) is
# still blocked: the SQL that would run is not what a person verified.
STRICT_USABLE = frozenset({TrustLabel.VERIFIED, TrustLabel.BUILT_FROM_VERIFIED})


class TrustInputs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    definition_states: list[DefinitionState]  # one per SQL definition used; [] for raw SQL
    # ADR 0026: the OWN state of each composed definition used (its parts' states are in
    # definition_states). Verified → its final SELECT and choice of parts were reviewed by
    # a person; llm_annotated → like an agent-written final SELECT
    # (built_from_verified_parts at best); draft → not usable.
    composed_states: list[DefinitionState] = Field(default_factory=list)
    raw_sql: bool = False  # check_and_run
    has_select: bool = False  # an agent-written final SELECT
    joins_known: bool = True  # every join in agent-written SQL is known
    issues: list[Issue] = Field(default_factory=list)  # from render/compose
    mode: TeamMode = TeamMode.OPEN


class TrustResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: TrustLabel
    reasons: list[str]  # plain language, one per rule that applied
    usable: bool  # False -> services refuse to run it
    blocking: list[Issue]  # draft_not_usable / strict_mode_blocked


def _worse(a: TrustLabel, b: TrustLabel) -> TrustLabel:
    return a if TRUST_ORDER.index(a) >= TRUST_ORDER.index(b) else b


def _step_down(label: TrustLabel) -> TrustLabel:
    return TRUST_ORDER[min(TRUST_ORDER.index(label) + 1, len(TRUST_ORDER) - 1)]


def _unique(issues: list[Issue]) -> list[Issue]:
    """Drop exact duplicates so one problem reported twice is not counted twice."""
    seen: set[Issue] = set()
    out: list[Issue] = []
    for issue in issues:
        if issue not in seen:
            seen.add(issue)
            out.append(issue)
    return out


def assess(inputs: TrustInputs) -> TrustResult:
    label = TrustLabel.VERIFIED
    reasons: list[str] = []
    states = [*inputs.definition_states, *inputs.composed_states]
    # A composed definition no person has verified combines its parts the way an agent's
    # final SELECT does (ADR 0026): same cap, same rule for unknown joins.
    unreviewed_composition = any(s is not DefinitionState.VERIFIED for s in inputs.composed_states)
    has_select = inputs.has_select or unreviewed_composition

    # Base: agent-written raw SQL, or definitions used as-is.
    if inputs.raw_sql or not states:
        if inputs.joins_known:
            label = TrustLabel.NEW_LOGIC_KNOWN_JOINS
            reasons.append("New SQL logic written by an agent; every join is a known join.")
        else:
            label = TrustLabel.EXPLORATORY
            reasons.append("New SQL logic written by an agent with joins that aren't known.")
    elif all(s is DefinitionState.VERIFIED for s in states):
        reasons.append("Every definition used is verified.")

    if inputs.has_select and not inputs.raw_sql and states:
        label = _worse(label, TrustLabel.BUILT_FROM_VERIFIED)
        reasons.append("An agent-written final SELECT combines the definitions.")
    if unreviewed_composition and not inputs.raw_sql:
        label = _worse(label, TrustLabel.BUILT_FROM_VERIFIED)
        reasons.append(
            "Uses a composed definition no person has verified as a whole (its parts are "
            "labeled on their own)."
        )

    if DefinitionState.LLM_ANNOTATED in inputs.definition_states:
        label = _worse(label, TrustLabel.LLM_ANNOTATED)
        reasons.append("Uses a definition that passed checks but no person has reviewed.")

    if DefinitionState.DRAFT in states:
        label = TrustLabel.EXPLORATORY
        reasons.append("Uses a draft definition (checks failed or not run).")

    unique_issues = _unique(inputs.issues)
    if any(i.code is IssueCode.UNKNOWN_JOIN for i in unique_issues):
        label = TrustLabel.EXPLORATORY
        reasons.append("Joins on keys that aren't in the join map.")
    elif has_select and not inputs.joins_known and not inputs.raw_sql:
        label = TrustLabel.EXPLORATORY
        reasons.append("The final SELECT joins on keys that aren't known.")

    downgrades: list[str] = []
    for issue in unique_issues:
        what = DOWNGRADE_CODES.get(issue.code)
        if what is None:
            continue
        downgrades.append(what)
        before = label
        label = _step_down(label)
        if label is not before:
            reasons.append(f"Lowered one step: {what}.")
        else:
            reasons.append(f"Already at the lowest label; also {what}.")

    blocking: list[Issue] = []
    if DefinitionState.DRAFT in states:
        blocking.append(
            Issue(
                code=IssueCode.DRAFT_NOT_USABLE,
                severity=Severity.ERROR,
                message=(
                    "A draft definition can't be used. Fix its failing checks or pick a "
                    "verified or llm_annotated definition."
                ),
            )
        )
    if inputs.mode is TeamMode.STRICT and label not in STRICT_USABLE:
        blocking.append(
            Issue(
                code=IssueCode.STRICT_MODE_BLOCKED,
                severity=Severity.ERROR,
                message=(
                    f"Strict mode only runs verified results; this one would be labeled "
                    f"'{label.value}'. Use verified definitions without overrides, or ask a "
                    f"person to verify what you need."
                ),
            )
        )
    elif inputs.mode is TeamMode.STRICT and downgrades:
        blocking.append(
            Issue(
                code=IssueCode.STRICT_MODE_BLOCKED,
                severity=Severity.ERROR,
                message=(
                    f"Strict mode only runs verified SQL as it was verified, but "
                    f"{'; '.join(downgrades)}. Use the verified definitions without "
                    f"overrides, allowed values only and in their own dialect, or ask a "
                    f"person to verify what you need."
                ),
            )
        )

    return TrustResult(label=label, reasons=reasons, usable=not blocking, blocking=blocking)
