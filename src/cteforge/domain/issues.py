"""Structured problems and warnings (DESIGN §3 "Issue", ADR 0015).

Every check, render, compose and run returns Issues instead of free text, so agents and the
GUI can act on them. Codes are append-only: add new members at the end of their group.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Severity(StrEnum):
    ERROR = "error"  # Blocks: the definition stays draft / the call fails.
    WARNING = "warning"  # Allowed, but lowers trust or needs a person's attention.
    INFO = "info"


class IssueCode(StrEnum):
    # Import / annotation (parse_query, propose_definition)
    RANGE_NOT_SIMPLE = "range_not_simple"
    DESCRIPTION_REQUIRED = "description_required"
    AMBIGUOUS_DATE_FORMAT = "ambiguous_date_format"
    TYPE_UNKNOWN = "type_unknown"
    TYPE_MISMATCH = "type_mismatch"
    ROUNDTRIP_SQL_DIFFERS = "roundtrip_sql_differs"
    ROUNDTRIP_RESULTS_DIFFER = "roundtrip_results_differ"
    CHECKS_INCOMPLETE = "checks_incomplete"  # e.g. no warehouse: results/types unchecked
    COMPILE_FAILED = "compile_failed"
    DIALECT_MISMATCH = "dialect_mismatch"
    GRAIN_PARTIAL = "grain_partial"  # A grain variable leaves same-unit truncations as written.
    # Render
    FIXED_OVERRIDE = "fixed_override"
    VALUE_NOT_ALLOWED = "value_not_allowed"
    TRANSPILED = "transpiled"
    # Compose / run
    UNKNOWN_ALIAS = "unknown_alias"
    UNKNOWN_JOIN = "unknown_join"
    FAN_OUT_RISK = "fan_out_risk"
    NOT_READ_ONLY = "not_read_only"
    ROW_LIMIT_REACHED = "row_limit_reached"
    DRAFT_NOT_USABLE = "draft_not_usable"
    STRICT_MODE_BLOCKED = "strict_mode_blocked"
    RUN_FAILED = "run_failed"
    UNUSED_ALIAS = "unused_alias"
    # Proposals / review / revalidation
    STALE_BASE_VERSION = "stale_base_version"
    PERMISSION_DENIED = "permission_denied"
    NOT_FOUND = "not_found"
    DRIFT = "drift"
    RECIPE_INVALID = "recipe_invalid"  # A proposed recipe can't be rendered or composed.


class Issue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: IssueCode
    severity: Severity
    message: str  # Plain language; says what to do next where possible.
    variable: str | None = None
    literal: str | None = None  # Literal ID, e.g. "closed_deals.L1".


def has_errors(issues: list[Issue]) -> bool:
    return any(i.severity is Severity.ERROR for i in issues)
