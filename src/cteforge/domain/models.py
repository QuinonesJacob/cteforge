"""Core data models (DESIGN §3). Pure pydantic; no SQL library imports."""

from __future__ import annotations

import datetime as dt
import re
from enum import StrEnum
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from cteforge.domain.dialects import Dialect
from cteforge.domain.variables import RangeShape, Role, VariableType

SCHEMA_VERSION = 1
_IDENT = re.compile(r"^[a-z][a-z0-9_]*$")


def _check_ident(value: str) -> str:
    if not _IDENT.match(value) or "__" in value:
        raise ValueError(
            f"'{value}' must be lower_snake_case (letters, digits, single underscores)"
        )
    return value


Ident = Annotated[str, AfterValidator(_check_ident)]


class DefinitionState(StrEnum):
    """DESIGN §8. Only a person may move a definition to VERIFIED."""

    DRAFT = "draft"
    LLM_ANNOTATED = "llm_annotated"
    VERIFIED = "verified"


class TrustLabel(StrEnum):
    """Label on every result (DESIGN §8). Policy lives in trust/ (M2)."""

    VERIFIED = "verified"
    BUILT_FROM_VERIFIED = "built_from_verified_parts"
    NEW_LOGIC_KNOWN_JOINS = "new_logic_on_known_joins"
    EXPLORATORY = "exploratory"


class LiteralForm(StrEnum):
    """How a literal appeared in the trusted SQL, so rendering can reproduce it."""

    BARE = "bare"  # '2026-01-01'
    TYPED = "typed"  # TIMESTAMP '2026-01-01' or CAST('2026-01-01' AS TIMESTAMP)


class Annotation(BaseModel):
    """What a person or LLM says about some literals: "these are variable X".

    `literals` are literal IDs reported by parsing.analyze (e.g. "closed_deals.L1").
    date_range takes [start_id, end_id]; text_list takes every literal of one IN list.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Ident
    type: VariableType
    role: Role = Role.FIXED  # Nothing becomes adjustable unless someone says so.
    literals: list[str] = Field(min_length=1)
    column: str | None = None  # What it filters, e.g. "deals.closed_at". Optional hint.
    description: str = ""
    allowed: list[Any] | None = None


class VariableSpec(BaseModel):
    """A typed hole in a definition's template (DESIGN §4)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Ident
    type: VariableType
    role: Role
    column: str | None = None
    column_type: str | None = None  # From the warehouse, never from an LLM (ADR 0006).
    literal_form: LiteralForm = LiteralForm.BARE
    cast_type: str | None = None  # Source-dialect type when literal_form is TYPED.
    shape: RangeShape | None = None  # date_range only.
    original_value: Any  # Plain (JSON-friendly) value from the trusted SQL.
    allowed: list[Any] | None = None
    description: str = ""


class Definition(BaseModel):
    """A trusted query (M1: whole query; M2: one CTE) with its typed variables."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = SCHEMA_VERSION
    id: Ident
    version: int = 1
    title: str
    meaning: str = ""
    keywords: list[str] = Field(default_factory=list)
    use_when: str = ""
    dont_use_when: str = ""
    source_dialect: Dialect
    template: str  # SQL with :name placeholders, in source_dialect (ADR 0011).
    variables: list[VariableSpec] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    grain: str | None = None
    state: DefinitionState = DefinitionState.DRAFT
    verified_by: str | None = None
    verified_at: dt.date | None = None

    def variable(self, name: str) -> VariableSpec | None:
        return next((v for v in self.variables if v.name == name), None)


class JoinEdge(BaseModel):
    """Join map entry (DESIGN §3). Populated from M2."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    left: str
    right: str
    condition: str
    relationship: str | None = None  # one_to_many, many_to_one, ...
    usage_count: int = 0
    traps: list[str] = Field(default_factory=list)


class Recipe(BaseModel):
    """Saved query as definitions + values (DESIGN §3). Used from M2."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    title: str
    uses: dict[str, str]  # alias -> definition id
    values: dict[str, dict[str, Any]] = Field(default_factory=dict)
    select: str | None = None
