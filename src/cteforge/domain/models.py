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
    LLM_ANNOTATED = "llm_annotated"
    NEW_LOGIC_KNOWN_JOINS = "new_logic_on_known_joins"
    EXPLORATORY = "exploratory"


# Best to worst (DESIGN §8). A downgrade moves one step right.
TRUST_ORDER = [
    TrustLabel.VERIFIED,
    TrustLabel.BUILT_FROM_VERIFIED,
    TrustLabel.LLM_ANNOTATED,
    TrustLabel.NEW_LOGIC_KNOWN_JOINS,
    TrustLabel.EXPLORATORY,
]


class TeamMode(StrEnum):
    """DESIGN §8: strict = only verified usable; open = llm_annotated usable, labeled."""

    STRICT = "strict"
    OPEN = "open"


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
    template: str  # SQL with named placeholders in source_dialect syntax (ADR 0011).
    variables: list[VariableSpec] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    grain: str | None = None  # Plain language, e.g. "one row per account per month".
    join_keys: list[str] = Field(default_factory=list)  # Output columns safe to join on.
    owner: str | None = None  # User who may approve changes to it (DESIGN §9).
    profile: str | None = None  # Warehouse profile it runs on (ADR 0020).
    state: DefinitionState = DefinitionState.DRAFT
    verified_by: str | None = None
    verified_at: dt.date | None = None

    def variable(self, name: str) -> VariableSpec | None:
        return next((v for v in self.variables if v.name == name), None)


class DefinitionVersionInfo(BaseModel):
    """One row of a definition's history (DESIGN §15)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    version: int
    state: DefinitionState
    author: str
    created_at: dt.datetime
    change_note: str = ""
    logic_changed: bool = True  # False for metadata-only edits (keywords, notes).
    verified_by: str | None = None
    verified_at: dt.date | None = None


class JoinKey(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    left: str  # column on left_table
    right: str  # column on right_table


class JoinEdge(BaseModel):
    """Join map entry (DESIGN §3): mined from trusted SQL or proposed. A reference, not a gate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str  # Deterministic: "<left_table>__<right_table>__<keys>" normalized, lowercase.
    left_table: str
    right_table: str
    keys: list[JoinKey] = Field(min_length=1)
    join_type: str = "inner"  # inner, left, right, full
    relationship: str | None = None  # one_to_one, one_to_many, many_to_one, many_to_many
    usage_count: int = 0  # How many definitions use it.
    traps: list[str] = Field(default_factory=list)  # e.g. "fan-out: one deal has many invoices"
    state: DefinitionState = DefinitionState.LLM_ANNOTATED


class DefinitionRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: Ident
    version: int | None = None  # None = float on the latest usable version (DESIGN §15).


class Recipe(BaseModel):
    """Saved query as definitions + values + optional final SELECT (DESIGN §3, §17)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = SCHEMA_VERSION
    id: Ident
    version: int = 1
    title: str
    description: str = ""
    uses: dict[str, DefinitionRef]  # alias -> definition
    values: dict[str, dict[str, Any]] = Field(default_factory=dict)  # alias -> var -> value
    select: str | None = None  # Final SELECT over the aliases, in `dialect`.
    dialect: Dialect
    state: DefinitionState = DefinitionState.DRAFT
