"""Response models shared by use-cases and adapters (MCP and HTTP return them verbatim).

All models are frozen pydantic and JSON-friendly (`model_dump(mode="json")`). Builders
turn domain objects into cards; `json_safe` / `json_rows` make warehouse values JSON-safe.
"""

from __future__ import annotations

import base64
import datetime as dt
import difflib
import math
import uuid
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from enum import Enum
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from cteforge.domain.audit import AuditEvent
from cteforge.domain.codec import definition_to_yaml
from cteforge.domain.dialects import Dialect
from cteforge.domain.issues import Issue
from cteforge.domain.models import (
    Definition,
    DefinitionState,
    DefinitionVersionInfo,
    JoinEdge,
    Recipe,
    TeamMode,
    TrustLabel,
    VariableSpec,
)
from cteforge.domain.proposals import Proposal
from cteforge.domain.variables import RangeShape, Role, VariableType, expected_format
from cteforge.parsing.analyze import QueryAnalysis

_CARD = ConfigDict(extra="forbid", frozen=True)


# ── JSON-safe values ─────────────────────────────────────────────────────────────────


def _decimal(value: Decimal) -> Any:
    """Integral → int; exactly representable as float → float; otherwise str (no silent
    precision loss). Non-finite → str."""
    if not value.is_finite():
        return str(value)
    if value == value.to_integral_value():
        return int(value)
    as_float = float(value)
    return as_float if Decimal(repr(as_float)) == value else str(value)


def json_safe(value: Any) -> Any:
    """A warehouse value as plain JSON: enums → their value, dates/times → ISO text,
    Decimal → int/float/str, non-finite floats → str, bytes → base64 text, UUID → str,
    containers recursively, anything else unknown → str."""
    if isinstance(value, Enum):  # Before str/int: a StrEnum is not plain YAML/JSON.
        return json_safe(value.value)
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Decimal):
        return _decimal(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return value.total_seconds()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(v) for v in value]
    return str(value)


def json_rows(rows: Iterable[Sequence[Any]]) -> list[list[Any]]:
    return [[json_safe(v) for v in row] for row in rows]


# ── Definitions ──────────────────────────────────────────────────────────────────────


class VariableCard(BaseModel):
    """What an agent needs to fill one variable (DESIGN §4)."""

    model_config = _CARD

    name: str
    type: VariableType
    role: Role
    input_format: str  # domain.variables.expected_format(type): the strict input format.
    description: str = ""
    allowed: list[Any] | None = None
    unit: str | None = None
    format: str | None = None  # The analyst's explicit date format, if any (ADR 0014).
    column: str | None = None
    column_type: str | None = None
    shape: RangeShape | None = None
    original_value: Any = None
    places: int = 1  # How many places in the SQL this variable fills.


def variable_card(spec: VariableSpec) -> VariableCard:
    return VariableCard(
        name=spec.name,
        type=spec.type,
        role=spec.role,
        input_format=expected_format(spec.type),
        description=spec.description,
        allowed=json_safe(spec.allowed) if spec.allowed is not None else None,
        unit=spec.unit,
        format=spec.format,
        column=spec.column,
        column_type=spec.column_type,
        shape=spec.shape,
        original_value=json_safe(spec.original_value),
        places=len(spec.places()),
    )


class DefinitionCard(BaseModel):
    """Full card for get_definition (DESIGN §6)."""

    model_config = _CARD

    id: str
    version: int
    title: str
    meaning: str = ""
    keywords: list[str] = Field(default_factory=list)
    use_when: str = ""
    dont_use_when: str = ""
    source_dialect: Dialect
    profile: str | None = None
    state: DefinitionState
    verified_by: str | None = None
    verified_at: dt.date | None = None
    owner: str | None = None
    grain: str | None = None
    join_keys: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)
    variables: list[VariableCard] = Field(default_factory=list)
    template: str
    history: list[DefinitionVersionInfo] | None = None


def card_from_definition(
    definition: Definition, history: Sequence[DefinitionVersionInfo] | None = None
) -> DefinitionCard:
    d = definition
    return DefinitionCard(
        id=d.id,
        version=d.version,
        title=d.title,
        meaning=d.meaning,
        keywords=list(d.keywords),
        use_when=d.use_when,
        dont_use_when=d.dont_use_when,
        source_dialect=d.source_dialect,
        profile=d.profile,
        state=d.state,
        verified_by=d.verified_by,
        verified_at=d.verified_at,
        owner=d.owner,
        grain=d.grain,
        join_keys=list(d.join_keys),
        outputs=list(d.outputs),
        tables=list(d.tables),
        variables=[variable_card(v) for v in d.variables],
        template=d.template,
        history=list(history) if history is not None else None,
    )


class SearchHit(BaseModel):
    model_config = _CARD

    id: str
    version: int
    title: str
    meaning: str = ""
    state: DefinitionState
    score: float
    keywords: list[str] = Field(default_factory=list)
    variables: list[VariableCard] = Field(default_factory=list)


def search_hit(definition: Definition, score: float) -> SearchHit:
    return SearchHit(
        id=definition.id,
        version=definition.version,
        title=definition.title,
        meaning=definition.meaning,
        state=definition.state,
        score=score,
        keywords=list(definition.keywords),
        variables=[variable_card(v) for v in definition.variables],
    )


# ── parse_query ──────────────────────────────────────────────────────────────────────


class ParseReport(BaseModel):
    """parse_query result: literals with IDs, structure, suggestions (DESIGN §6)."""

    model_config = _CARD

    dialect: Dialect
    tables: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    ctes: list[dict[str, Any]] = Field(default_factory=list)  # CteInfo
    literals: list[dict[str, Any]] = Field(default_factory=list)  # LiteralInfo
    ranges: list[dict[str, Any]] = Field(default_factory=list)  # RangeCandidate
    joins: list[dict[str, Any]] = Field(default_factory=list)  # JoinInfo
    grains: list[dict[str, Any]] = Field(default_factory=list)  # When analysis reports them.
    schema_found: bool = False  # Column types came from a warehouse.
    issues: list[Issue] = Field(default_factory=list)


def _dumps(items: Iterable[BaseModel]) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in items]


def parse_report(
    analysis: QueryAnalysis, *, schema_found: bool, issues: Sequence[Issue] = ()
) -> ParseReport:
    grains = getattr(analysis, "grains", None) or []
    return ParseReport(
        dialect=analysis.dialect,
        tables=list(analysis.tables),
        outputs=list(analysis.outputs),
        ctes=_dumps(analysis.ctes),
        literals=_dumps(analysis.literals),
        ranges=_dumps(analysis.ranges),
        joins=_dumps(analysis.joins),
        grains=_dumps(grains),
        schema_found=schema_found,
        issues=list(issues),
    )


# ── Runs ─────────────────────────────────────────────────────────────────────────────


class UsedDefinition(BaseModel):
    """One definition version a run used, with who verified it and when (DESIGN §8:
    a verified result is labeled with verifier + date)."""

    model_config = _CARD

    id: str
    version: int
    state: DefinitionState
    verified_by: str | None = None
    verified_at: dt.date | None = None


def used_definition(definition: Definition) -> UsedDefinition:
    return UsedDefinition(
        id=definition.id,
        version=definition.version,
        state=definition.state,
        verified_by=definition.verified_by,
        verified_at=definition.verified_at,
    )


class RunOutcome(BaseModel):
    """compile_and_run / check_and_run result. `sql` is the tagged SQL that was (or would
    be) executed; `rows` are JSON-safe (see `json_rows`)."""

    model_config = _CARD

    sql: str
    dialect: Dialect
    label: TrustLabel
    reasons: list[str] = Field(default_factory=list)
    usable: bool
    issues: list[Issue] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    ran: bool = False
    compile_id: str | None = None
    definitions: list[str] = Field(default_factory=list)  # "id@version"
    used: list[UsedDefinition] = Field(default_factory=list)  # state + verifier per version
    duration_ms: int | None = None


# ── Revalidation ─────────────────────────────────────────────────────────────────────


class DefinitionDrift(BaseModel):
    """revalidate result for one definition version (DESIGN §10, §18)."""

    model_config = _CARD

    id: str
    version: int
    state: DefinitionState  # Unchanged by revalidate; reported for context.
    profile: str | None = None
    checked: bool  # False when no warehouse check could run.
    ok: bool  # No `drift` issue.
    results_match: bool | None = None
    issues: list[Issue] = Field(default_factory=list)


# ── Settings ─────────────────────────────────────────────────────────────────────────


class TeamSettings(BaseModel):
    """Team settings (DESIGN §8)."""

    model_config = _CARD

    mode: TeamMode


# ── Proposals ────────────────────────────────────────────────────────────────────────


class ProposalView(Proposal):
    """A proposal plus what a reviewer needs: a unified diff against its base and the
    impact preview (ids of recipes using the target definition)."""

    diff: str = ""
    impact: list[str] = Field(default_factory=list)


def _yaml(data: Any) -> str:
    return yaml.safe_dump(json_safe(data), sort_keys=False, allow_unicode=True)


def _proposed_text(proposal: Proposal) -> str:
    payload = proposal.payload
    if "definition_yaml" in payload:
        return str(payload["definition_yaml"])
    if "recipe" in payload:
        return _yaml(payload["recipe"])
    if "join" in payload:
        return _yaml(payload["join"])
    return _yaml(payload)


def _base_text(base: Definition | Recipe | JoinEdge | None) -> str:
    if base is None:
        return ""
    if isinstance(base, Definition):
        return definition_to_yaml(base)
    return _yaml(base.model_dump(mode="json"))


def proposal_diff(proposal: Proposal, base: Definition | Recipe | JoinEdge | None) -> str:
    """Unified diff of the stored form (codec YAML for definitions) of `base` → proposal.
    No base (a new object) → every line is an addition."""
    old = _base_text(base)
    new = _proposed_text(proposal)
    if base is None:
        old_name = "/dev/null"
    elif isinstance(base, JoinEdge):
        old_name = proposal.target_id
    else:
        old_name = f"{proposal.target_id}@v{base.version}"
    lines = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=old_name,
        tofile=f"{proposal.target_id} (proposed)",
    )
    return "".join(line if line.endswith("\n") else line + "\n" for line in lines)


def proposal_view(
    proposal: Proposal,
    base: Definition | Recipe | JoinEdge | None = None,
    impact: Iterable[str] = (),
) -> ProposalView:
    return ProposalView(
        **proposal.model_dump(),
        diff=proposal_diff(proposal, base),
        impact=sorted(set(impact)),
    )


class ProposalOutcome(BaseModel):
    """propose_* / revise_definition result. `definition` is set once applied."""

    model_config = _CARD

    proposal: ProposalView
    definition: DefinitionCard | None = None
    issues: list[Issue] = Field(default_factory=list)
    auto_applied: bool = False


# ── Audit ────────────────────────────────────────────────────────────────────────────


class AuditPage(BaseModel):
    """One page of audit events, newest first. `has_more` when the page is full."""

    model_config = _CARD

    events: list[AuditEvent] = Field(default_factory=list)
    limit: int
    has_more: bool = False


def audit_page(events: Sequence[AuditEvent], limit: int) -> AuditPage:
    return AuditPage(events=list(events), limit=limit, has_more=len(events) >= limit > 0)
