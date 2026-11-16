"""Audit events: cteforge's own calls, never result rows (DESIGN §11, ADR 0008)."""

from __future__ import annotations

import datetime as dt
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from cteforge.domain.models import TrustLabel
from cteforge.domain.users import ActorKind


class AuditKind(StrEnum):
    SEARCH = "search"
    GET_DEFINITION = "get_definition"
    PARSE_QUERY = "parse_query"
    COMPILE_AND_RUN = "compile_and_run"
    CHECK_AND_RUN = "check_and_run"
    PROPOSE = "propose"
    REVIEW = "review"
    REVALIDATE = "revalidate"
    ADMIN = "admin"


class AuditOutcome(StrEnum):
    OK = "ok"
    ERROR = "error"
    REJECTED = "rejected"  # Refused by policy (permissions, strict mode, not read-only).


class AuditEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str  # uuid4 hex
    at: dt.datetime
    actor: str
    actor_kind: ActorKind
    kind: AuditKind
    question: str | None = None  # The user's question, if the agent passed one.
    definitions: list[str] = Field(default_factory=list)  # "id@version"
    values: dict[str, Any] = Field(default_factory=dict)
    trust_label: TrustLabel | None = None
    compile_id: str | None = None  # Matches the /* cteforge: ... */ tag in warehouse logs.
    sql_sha256: str | None = None  # Hash of the executed SQL, not the SQL text.
    row_count: int | None = None
    duration_ms: int | None = None
    outcome: AuditOutcome = AuditOutcome.OK
    error: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
