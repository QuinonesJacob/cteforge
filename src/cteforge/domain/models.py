"""Core data models (DESIGN §3). Pure pydantic; no SQL library imports."""

from __future__ import annotations

import datetime as dt
import re
from enum import StrEnum
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from cteforge.domain.dialects import Dialect
from cteforge.domain.variables import PLACEHOLDER_FIELDS, RangeShape, Role, VariableType

SCHEMA_VERSION = 1
_IDENT = re.compile(r"^[a-z][a-z0-9_]*$")


def _check_ident(value: str) -> str:
    if not _IDENT.match(value) or "__" in value:
        raise ValueError(
            f"'{value}' must be lower_snake_case (letters, digits, single underscores)"
        )
    return value


Ident = Annotated[str, AfterValidator(_check_ident)]
_DATE_TYPES = (VariableType.DATE, VariableType.DATE_RANGE)


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
    """What a person or LLM says about some literals: "these are variable X" (ADR 0015).

    `literals` are literal IDs reported by parsing.analyze (e.g. "closed_deals.L1"),
    grouped into *places* — occurrences of the same filter (e.g. one period used in two
    CTEs). Every place must hold the same original value.
    - date_range: [start, end] pairs in order; two places = [s1, e1, s2, e2].
    - text_list: every literal of one IN list, in order. Several lists may follow each
      other; a new place starts where the IN group (LiteralInfo.in_list) changes.
    - date, text, number, boolean: one literal per place.
    - grain: grain IDs (`<scope>.G<n>`, QueryAnalysis.grains), one truncation node per
      place; every place must truncate to the same original unit (M1.5).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Ident
    type: VariableType
    role: Role = Role.FIXED  # Nothing becomes adjustable unless someone says so.
    literals: list[str] = Field(min_length=1)
    column: str | None = None  # What it filters, e.g. "deals.closed_at". Optional hint.
    description: str = ""
    allowed: list[Any] | None = None
    # date/date_range only: strftime pattern of bare non-ISO date text, e.g. "%m-%d-%Y"
    # (ADR 0014). Overrides the format parsing suggests; applies to bare places only.
    format: str | None = None
    unit: str | None = None  # number only: what the number means, e.g. "TFLOPS" (ADR 0014).


class VariableSlot(BaseModel):
    """One placeholder in the template that a variable fills: one literal position of the
    trusted SQL (one bound of a date_range, a whole IN list for text_list).

    A place is `len(PLACEHOLDER_FIELDS[type])` consecutive slots; slots carry their own
    form, cast and format so different places of one variable may be written differently.

    grain (ADR 0011, M1.5): `placeholder` only names the slot; nothing is written into the
    template. The truncation node stays as written there and `literals` holds its grain ID.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    placeholder: str  # Name in the template (variables.placeholder_name), e.g. period__start.
    field: str = ""  # date_range: "start" / "end"; "" otherwise.
    literals: list[str] = Field(default_factory=list)  # Literal IDs it replaced (provenance).
    literal_form: LiteralForm = LiteralForm.BARE
    cast_type: str | None = None  # Source-dialect type when literal_form is TYPED.
    format: str | None = None  # strftime pattern for bare date text; None = ISO YYYY-MM-DD.
    original_text: str | None = None  # number: literal as written ("1000.50", "1e3"), so
    #                                   the original value renders back exactly.
    #                                   grain: the original unit ("month"); the truncation
    #                                   node it names is rebuilt only for another unit.


class VariableSpec(BaseModel):
    """A typed hole in a definition's template (DESIGN §4).

    Agents see name, type, role, description, column, shape, unit, allowed and
    original_value. `slots` say where and how the value is written into the template.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Ident
    type: VariableType
    role: Role
    column: str | None = None  # What the first place filters, e.g. "deals.closed_at".
    column_type: str | None = None  # Compared type from the warehouse, never from an LLM
    #                                 (ADR 0006); the expression's type when wrapped.
    shape: RangeShape | None = None  # date_range only; None = not a suggested pair (ADR 0015).
    format: str | None = None  # The analyst's explicit format (Annotation.format), if any.
    unit: str | None = None  # number only.
    original_value: Any  # Plain (JSON-friendly) value from the trusted SQL.
    allowed: list[Any] | None = None
    description: str = ""
    slots: list[VariableSlot] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> VariableSpec:
        if self.format is not None and self.type not in _DATE_TYPES:
            raise ValueError(f"format applies to date and date_range only, not {self.type}")
        if self.unit is not None and self.type is not VariableType.NUMBER:
            raise ValueError(f"unit applies to number only, not {self.type}")
        if self.type is VariableType.GRAIN and any(
            s.original_text is None or len(s.literals) != 1 for s in self.slots
        ):
            raise ValueError("each grain slot names one grain ID and its original unit")
        fields = PLACEHOLDER_FIELDS[self.type]
        if [s.field for s in self.slots] != list(fields) * (len(self.slots) // len(fields)):
            raise ValueError(f"slots of a {self.type} must repeat the fields {list(fields)}")
        return self

    def places(self) -> list[list[VariableSlot]]:
        """Slots grouped by place (each group follows PLACEHOLDER_FIELDS[type])."""
        size = len(PLACEHOLDER_FIELDS[self.type])
        return [self.slots[i : i + size] for i in range(0, len(self.slots), size)]


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
    # SQL with named placeholders written in source_dialect syntax (ADR 0011), e.g.
    # `$period__start` (DuckDB), `:period__start` (Snowflake); see VariableSlot.placeholder.
    template: str
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


class FormerId(BaseModel):
    """An ID a definition no longer has (ADR 0024). `definition_id` is its CURRENT id (a
    chain a → b → c points `a` and `b` at `c`); `renamed_to` the id it got by that rename.
    A former ID stays searchable, its lookups fail naming the current id, and no other
    definition may take it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    former_id: str
    definition_id: str
    renamed_to: str
    renamed_by: str
    renamed_at: dt.datetime
    proposal_id: str | None = None


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

    def uses_definition(self, id: str) -> bool:
        return any(ref.id == id for ref in self.uses.values())

    def with_definition_renamed(self, old: str, new: str) -> Recipe:
        """This recipe with every use of definition `old` pointing at `new` (ADR 0024);
        pinned versions are kept (a rename keeps version numbers)."""
        uses = {
            alias: ref.model_copy(update={"id": new}) if ref.id == old else ref
            for alias, ref in self.uses.items()
        }
        return self.model_copy(update={"uses": uses})
