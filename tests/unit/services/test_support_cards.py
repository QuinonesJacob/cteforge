from __future__ import annotations

import datetime as dt
import json
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from cteforge.domain.audit import AuditEvent, AuditKind
from cteforge.domain.codec import definition_to_yaml
from cteforge.domain.dialects import Dialect
from cteforge.domain.issues import Issue, IssueCode, Severity
from cteforge.domain.models import (
    Composition,
    Definition,
    DefinitionRef,
    DefinitionState,
    DefinitionVersionInfo,
    JoinEdge,
    JoinKey,
    TrustLabel,
)
from cteforge.domain.proposals import Proposal, ProposalKind
from cteforge.domain.users import ActorKind
from cteforge.domain.variables import VariableType, expected_format
from cteforge.files.definitions import load_definition
from cteforge.parsing.analyze import analyze
from cteforge.services.cards import (
    AuditPage,
    DefinitionCard,
    ProposalOutcome,
    RunOutcome,
    audit_page,
    card_from_definition,
    json_rows,
    json_safe,
    parse_report,
    proposal_view,
    search_hit,
    variable_card,
)

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"
NOW = dt.datetime(2026, 11, 16, 8, 0, tzinfo=dt.UTC)


@pytest.fixture(scope="module")
def definition() -> Definition:
    return load_definition(EXAMPLES / "definitions" / "realization_rate.yml")


def _proposal(kind: ProposalKind, target: str, payload: dict, base: int | None = None) -> Proposal:
    return Proposal(
        id=uuid.uuid4().hex,
        kind=kind,
        target_id=target,
        base_version=base,
        payload=payload,
        checks=[Issue(code=IssueCode.CHECKS_INCOMPLETE, severity=Severity.WARNING, message="m")],
        author="bot",
        author_kind=ActorKind.AGENT,
        created_at=NOW,
    )


# ── json_safe ───────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (True, True),
        (7, 7),
        ("x", "x"),
        (1.5, 1.5),
        (float("nan"), "nan"),
        (float("inf"), "inf"),
        (Decimal("100"), 100),
        (Decimal("100.00"), 100),
        (Decimal("1000.5"), 1000.5),
        (Decimal("0.1"), 0.1),
        (Decimal("12345678901234567890.123456789"), "12345678901234567890.123456789"),
        (Decimal("NaN"), "NaN"),
        (dt.date(2026, 1, 2), "2026-01-02"),
        (dt.datetime(2026, 1, 2, 3, 4, 5), "2026-01-02T03:04:05"),
        (dt.time(3, 4), "03:04:00"),
        (dt.timedelta(minutes=2), 120.0),
        (b"\x00\xff", "AP8="),
        (uuid.UUID(int=1), "00000000-0000-0000-0000-000000000001"),
        ({"a": Decimal("2")}, {"a": 2}),
        ((1, dt.date(2026, 1, 1)), [1, "2026-01-01"]),
    ],
)
def test_json_safe(value: object, expected: object) -> None:
    assert json_safe(value) == expected


def test_json_rows_are_json_serializable() -> None:
    rows = json_rows([(Decimal("1.25"), dt.date(2026, 3, 1), None), (Decimal("3"), b"a", "t")])
    assert rows == [[1.25, "2026-03-01", None], [3, "YQ==", "t"]]
    json.dumps(rows)


# ── definitions ─────────────────────────────────────────────────────────────────────


def test_variable_card_fields(definition: Definition) -> None:
    spec = definition.variable("regions")
    assert spec is not None
    card = variable_card(spec)
    assert card.name == "regions" and card.type is VariableType.TEXT_LIST
    assert card.input_format == expected_format(VariableType.TEXT_LIST)
    assert card.allowed == ["EMEA", "NA", "APAC", "LATAM"]
    assert card.original_value == ["EMEA", "NA"]
    assert card.places == 1
    period = variable_card(definition.variable("period"))  # type: ignore[arg-type]
    assert period.input_format == expected_format(VariableType.DATE_RANGE)
    assert period.shape == definition.variable("period").shape  # type: ignore[union-attr]


def test_card_from_definition_mirrors_definition(definition: Definition) -> None:
    card = card_from_definition(definition)
    assert card.id == definition.id and card.version == definition.version
    assert card.template == definition.template
    assert [v.name for v in card.variables] == [v.name for v in definition.variables]
    assert card.state is definition.state and card.history is None
    dumped = card.model_dump(mode="json")
    json.dumps(dumped)
    assert DefinitionCard.model_validate(dumped) == card


def test_card_with_history(definition: Definition) -> None:
    info = DefinitionVersionInfo(
        id=definition.id, version=1, state=DefinitionState.VERIFIED, author="jq", created_at=NOW
    )
    card = card_from_definition(definition, history=[info])
    assert card.history == [info]


def test_card_is_frozen(definition: Definition) -> None:
    card = card_from_definition(definition)
    with pytest.raises(ValidationError):
        card.title = "x"  # type: ignore[misc]


def test_search_hit(definition: Definition) -> None:
    hit = search_hit(definition, 2.5)
    assert (hit.id, hit.version, hit.score) == (definition.id, definition.version, 2.5)
    assert hit.keywords == definition.keywords
    assert {v.name for v in hit.variables} == {v.name for v in definition.variables}


# ── parse report ────────────────────────────────────────────────────────────────────


def test_parse_report_from_analysis() -> None:
    sql = (EXAMPLES / "queries" / "realization_rate.sql").read_text()
    analysis = analyze(sql, Dialect.DUCKDB)
    issue = Issue(code=IssueCode.CHECKS_INCOMPLETE, severity=Severity.INFO, message="no schema")
    report = parse_report(analysis, schema_found=False, issues=[issue])
    assert report.dialect is Dialect.DUCKDB
    assert report.tables == analysis.tables
    assert [lit["id"] for lit in report.literals] == [lit.id for lit in analysis.literals]
    assert [c["name"] for c in report.ctes] == [c.name for c in analysis.ctes]
    assert len(report.ranges) == len(analysis.ranges) and len(report.joins) == len(analysis.joins)
    assert report.schema_found is False and report.issues == [issue]
    json.dumps(report.model_dump(mode="json"))


# ── run outcome ─────────────────────────────────────────────────────────────────────


def test_run_outcome_round_trips_json() -> None:
    outcome = RunOutcome(
        sql="/* cteforge: x v1 compile_id=ab */\nSELECT 1",
        dialect=Dialect.DUCKDB,
        label=TrustLabel.VERIFIED,
        reasons=["one verified definition"],
        usable=True,
        columns=["a"],
        rows=json_rows([(Decimal("1.5"),)]),
        row_count=1,
        ran=True,
        compile_id="ab",
        definitions=["x@1"],
        duration_ms=3,
    )
    data = json.loads(json.dumps(outcome.model_dump(mode="json")))
    assert data["rows"] == [[1.5]] and data["label"] == "verified"
    assert RunOutcome.model_validate(data) == outcome


# ── proposals ───────────────────────────────────────────────────────────────────────


def test_proposal_view_new_definition_diff_is_all_additions(definition: Definition) -> None:
    text = definition_to_yaml(definition)
    p = _proposal(ProposalKind.NEW_DEFINITION, definition.id, {"definition_yaml": text})
    view = proposal_view(p, base=None, impact=[])
    lines = view.diff.splitlines()
    assert lines[0] == "--- /dev/null"
    body = [ln for ln in lines[3:]]
    assert body and all(ln.startswith("+") for ln in body)
    assert view.id == p.id and view.checks == p.checks and view.impact == []


def test_proposal_view_revision_diff_and_impact(definition: Definition) -> None:
    revised = definition.model_copy(
        update={"version": definition.version + 1, "title": "New title"}
    )
    p = _proposal(
        ProposalKind.REVISE_DEFINITION,
        definition.id,
        {"definition_yaml": definition_to_yaml(revised)},
        base=definition.version,
    )
    view = proposal_view(p, base=definition, impact=["r2", "r1", "r1"])
    assert f"--- {definition.id}@v{definition.version}" in view.diff
    assert f"-title: {definition.title}" in view.diff
    assert "+title: New title" in view.diff
    assert "+template" not in view.diff  # unchanged template is context only
    assert view.impact == ["r1", "r2"]


def test_proposal_view_identical_has_empty_diff(definition: Definition) -> None:
    p = _proposal(
        ProposalKind.REVISE_DEFINITION,
        definition.id,
        {"definition_yaml": definition_to_yaml(definition)},
        base=definition.version,
    )
    assert proposal_view(p, base=definition).diff == ""


def test_proposal_view_composed_definition_and_join() -> None:
    composed = Definition(
        id="r",
        title="R",
        template="SELECT * FROM a",
        composition=Composition(uses={"a": DefinitionRef(id="x")}),
        source_dialect=Dialect.DUCKDB,
    )
    changed = composed.model_copy(update={"version": 2, "title": "R2"})
    p = _proposal(
        ProposalKind.REVISE_DEFINITION, "r", {"definition_yaml": definition_to_yaml(changed)}
    )
    diff = proposal_view(p, base=composed).diff
    assert "--- r@v1" in diff and "-title: R" in diff and "+title: R2" in diff
    legacy = _proposal(ProposalKind.NEW_RECIPE, "old", {"recipe": {"id": "old", "title": "O"}})
    assert "+title: O" in proposal_view(legacy).diff  # legacy recipe proposals still render

    edge = JoinEdge(
        id="a__b__k", left_table="a", right_table="b", keys=[JoinKey(left="k", right="k")]
    )
    pj = _proposal(ProposalKind.NEW_JOIN, edge.id, {"join": edge.model_dump(mode="json")})
    jdiff = proposal_view(pj).diff
    assert "+left_table: a" in jdiff


def test_proposal_outcome_json(definition: Definition) -> None:
    p = _proposal(
        ProposalKind.NEW_DEFINITION,
        definition.id,
        {"definition_yaml": definition_to_yaml(definition)},
    )
    outcome = ProposalOutcome(
        proposal=proposal_view(p), definition=card_from_definition(definition), auto_applied=True
    )
    data = json.loads(json.dumps(outcome.model_dump(mode="json")))
    assert data["proposal"]["diff"].startswith("--- /dev/null")
    assert data["definition"]["id"] == definition.id
    assert ProposalOutcome.model_validate(data) == outcome


# ── audit page ──────────────────────────────────────────────────────────────────────


def test_audit_page() -> None:
    events = [
        AuditEvent(id=f"e{i}", at=NOW, actor="a", actor_kind=ActorKind.HUMAN, kind=AuditKind.SEARCH)
        for i in range(2)
    ]
    assert audit_page(events, 2).has_more is True
    assert audit_page(events, 5).has_more is False
    assert audit_page([], 0) == AuditPage(events=[], limit=0, has_more=False)
    json.dumps(audit_page(events, 2).model_dump(mode="json"))
