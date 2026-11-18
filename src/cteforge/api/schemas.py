"""Request bodies for the HTTP API (src/cteforge/api/CLAUDE.md). Responses reuse the
services' card models. Unknown fields are refused (422) so typos never pass silently.

Structured inputs the services already validate (metadata, annotations, recipes, uses,
values) are passed through as plain JSON; services raise the CteforgeError that the API
maps to a status code.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cteforge.domain.dialects import Dialect
from cteforge.domain.models import TeamMode
from cteforge.domain.users import ActorKind, UserRole

_REQUEST = ConfigDict(extra="forbid")


class ParseRequest(BaseModel):
    """`dialect` omitted → the connection's: the profile's, else the default connection's."""

    model_config = _REQUEST

    sql: str
    dialect: Dialect | None = None
    profile: str | None = None


class RunRequest(BaseModel):
    """compile_and_run: `uses` (alias → {id, version?}) or `recipe_id`."""

    model_config = _REQUEST

    uses: dict[str, Any] | None = None
    recipe_id: str | None = None
    values: dict[str, dict[str, Any]] | None = None
    select: str | None = None
    dialect: Dialect | None = None
    profile: str | None = None
    run: bool = True
    limit: int = Field(default=100, ge=1)
    question: str | None = None


class CheckRequest(BaseModel):
    model_config = _REQUEST

    sql: str
    profile: str | None = None
    run: bool = True
    limit: int = Field(default=100, ge=1)
    question: str | None = None


class RevalidateRequest(BaseModel):
    model_config = _REQUEST

    ids: list[str] | None = None


class ProposeDefinitionRequest(BaseModel):
    """`dialect` omitted → the connection's: the profile's, else the default connection's."""

    model_config = _REQUEST

    sql: str
    dialect: Dialect | None = None
    metadata: dict[str, Any]
    annotations: list[dict[str, Any]]
    profile: str | None = None
    summary: str = ""


class ReviseDefinitionRequest(BaseModel):
    model_config = _REQUEST

    base_version: int
    sql: str | None = None
    dialect: Dialect | None = None
    metadata: dict[str, Any] | None = None
    annotations: list[dict[str, Any]] | None = None
    summary: str = ""


class RenameDefinitionRequest(BaseModel):
    """Propose a new ID for a definition (ADR 0024). `base_version`, when given, must be its
    current version."""

    model_config = _REQUEST

    new_id: str
    base_version: int | None = None
    summary: str = ""


class ProposeJoinRequest(BaseModel):
    """keys: [{left, right}, ...] or [[left, right], ...]."""

    model_config = _REQUEST

    left_table: str
    right_table: str
    keys: list[dict[str, str] | list[str]]
    join_type: str = "inner"
    relationship: str | None = None
    traps: list[str] = Field(default_factory=list)
    summary: str = ""


class ProposeRecipeRequest(BaseModel):
    model_config = _REQUEST

    recipe: dict[str, Any]
    summary: str = ""


class ApproveRequest(BaseModel):
    model_config = _REQUEST

    verify: bool = False
    comment: str = ""


class RejectRequest(BaseModel):
    model_config = _REQUEST

    comment: str


class VerifyRequest(BaseModel):
    model_config = _REQUEST

    version: int


class CreateUserRequest(BaseModel):
    model_config = _REQUEST

    name: str
    role: UserRole
    kind: ActorKind = ActorKind.HUMAN


class SetRoleRequest(BaseModel):
    model_config = _REQUEST

    role: UserRole


class SettingsRequest(BaseModel):
    """Changes only the fields given: `mode`, and/or `default_connection` (a profile name;
    null clears it). At least one is required."""

    model_config = _REQUEST

    mode: TeamMode | None = None
    default_connection: str | None = None

    @model_validator(mode="after")
    def _something_to_change(self) -> SettingsRequest:
        if not self.model_fields_set:
            raise ValueError("give `mode` and/or `default_connection`")
        if "mode" in self.model_fields_set and self.mode is None:
            raise ValueError("`mode` cannot be null")
        return self
