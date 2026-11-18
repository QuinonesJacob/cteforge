"""Use-cases: compile_and_run (definitions by reference) and check_and_run (exploratory SQL).

DESIGN §6, §8, §11, §17; ADR 0005, 0007, 0008, 0013. Each needs the tool of its name (ADR 0027).

compile_and_run flow: resolve `uses` → load each definition → pick the warehouse profile
and target dialect → render each definition (templating; a composed definition through
services/_compose.py: its parts rendered and composed, recursively) → compose with the
optional final SELECT → check the final SELECT's joins against the join map → trust label
(trust.assess) → refuse if not usable → tag the SQL (ADR 0008) → EXPLAIN → run → audit →
RunOutcome.

Choices made here (documented contract):
- Versions: a pinned version is used as is (a pinned draft is refused by trust). A floating
  reference takes the newest version usable in the team mode (`_library.usable_states`:
  verified, or in open mode also llm_annotated); with none, the latest version (trust then
  says why it is refused).
- Profile: `profile`, else the definitions' own `profile` (all must agree), else the
  registry default. The target dialect is the profile's dialect. A `dialect` argument that
  differs is refused when `run=True` (the SQL could not run there); with `run=False` the
  SQL is only compiled for that dialect (DESIGN §12, ADR 0013: definitions in another
  source dialect are transpiled and labeled `transpiled`), without EXPLAIN, with a
  `checks_incomplete` warning naming why. With `run=False` and no warehouse configured at
  all, SQL is compiled for `dialect` (or the definitions' common source dialect) without
  EXPLAIN, with a `checks_incomplete` warning.
- Composed definitions (ADR 0026) run by id like any other: `uses={"r": {"id": "<id>"}}`;
  their variables are `<alias>.<var>` (`values={"r": {"orders.period": ...}}`, or per
  part `{"r": {"orders": {"period": ...}}}`). Trust: their parts' states count like
  definitions'; a composed definition no person verified caps the label at
  built_from_verified_parts and its final SELECT's joins are judged like an agent's
  (trust.labels `composed_states`). A floating part with no usable version makes the run
  unusable (`part_not_usable`).
- Final-SELECT joins: alias↔alias joins are known when compose says so (declared
  join_keys). A join with a raw-table side is known only when a KNOWN JoinEdge matches it
  (`_library.matching_edge`: same two tables, case-insensitive, an unqualified name
  matching a qualified one by its last part; exactly the same key pairs, in either
  orientation; `_library.known_edges`: mined from a definition, added or confirmed by a
  person — an edge an agent added counts only once a person approved it, ADR 0026). A raw
  table read outside any known join also makes joins unknown. Unknown → `unknown_join`
  warning, and the trust label falls to `exploratory`.
- Raw tables in a final SELECT (DESIGN §8, §17): reading a base table directly is new
  logic, not "built from verified parts", even when every join is known. The label is then
  assessed as raw SQL (`new_logic_on_known_joins` at best, capped further by the
  definitions' states), so strict mode refuses it. Permission (ADR 0027): such a final
  SELECT is exploratory SQL, so it also needs the check_and_run tool (PermissionDenied,
  audited, before anything reaches the warehouse) — also when it is the stored final
  SELECT of a composed definition used (verified or not, nested too; ADR 0026,
  `_compose.Compiled.reads_raw`); without raw tables compile_and_run alone suffices.
- Provenance: `RunOutcome.used` lists each definition version with its state and, when
  verified, `verified_by` / `verified_at` (DESIGN §8).
- Row limit: `limit` clamped to 1..profile.max_rows; the warehouse enforces it again.
  Rows are also capped at MAX_RESULT_BYTES of JSON. `row_limit_reached` warning when more
  rows existed or the size cap dropped some.
- A warehouse failure (EXPLAIN or run) returns `usable=False` with a `run_failed` issue:
  nothing ran, so there is no answer (never an empty one).
- `values_used`: alias → every variable's rendered value; a changeable variable the caller
  left out gets a `default_value_used` info issue (it kept the original value).
- Audit (fail closed for executed runs: the event is written before rows are returned):
  definitions as id@version, values actually used per alias, label, compile_id, SHA-256 of
  the tagged SQL, row count, duration. Never SQL text or rows. Refusals → outcome rejected;
  input errors raised to the caller → outcome per `_audit.failure_outcome` (PolicyError
  rejected, others error); warehouse failures → outcome error, class name only; the
  caller still gets the (connector-scrubbed) warehouse message in a `run_failed` issue.

check_and_run: raw SQL on a profile (default profile unless named). One read-only
SELECT/UNION only (compose.checks.ensure_query; else refused with `not_read_only`; SQL that
does not parse, including several statements, raises SqlParseError). Its joins
(parsing.analyze) are compared with the join map as above — on the BASE tables and key
columns behind each side (JoinInfo `*_base_table` / `base_keys`), as join mining does, so
SQL written with CTEs is judged like the flat equivalent — and every base table it reads
must be connected to the others through known joins: tables read with no known join
between them (a comma/CROSS join, LATERAL, a subquery in FROM or WHERE, a UNION of
different tables) make joins unknown. → `new_logic_on_known_joins` or `exploratory`
(trust.assess, raw_sql=True). Tagged `cteforge: exploratory`.
When the SQL is AST-equal (after sqlglot parsing in the profile's dialect) to the
original query of a definition usable in the team mode (its template rendered with its
original values, `_library.original_sql`; same source dialect), an info issue
`matches_definition` names it and says to call compile_and_run with that id instead, which
keeps the definition's trust label. The hint never changes the label.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from cteforge.audit.tag import new_compile_id, sql_tag, tag_sql
from cteforge.compose.assemble import Part, compose
from cteforge.compose.checks import ensure_query
from cteforge.domain.access import Tool
from cteforge.domain.audit import AuditKind, AuditOutcome
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import (
    AuditError,
    CteforgeError,
    PolicyError,
    RenderError,
    SqlParseError,
    WarehouseError,
)
from cteforge.domain.issues import Issue, IssueCode, Severity
from cteforge.domain.models import (
    Definition,
    DefinitionRef,
    JoinEdge,
    TeamMode,
    TrustLabel,
)
from cteforge.domain.users import User
from cteforge.parsing.analyze import JoinInfo, analyze, parse
from cteforge.services import _audit, _compose, _library
from cteforge.services.auth import require
from cteforge.services.cards import (
    RunOutcome,
    UsedDefinition,
    json_rows,
    json_safe,
    used_definition,
)
from cteforge.services.context import AppContext, team_mode
from cteforge.trust.labels import TrustInputs, TrustResult, assess


def _warning(code: IssueCode, message: str) -> Issue:
    return Issue(code=code, severity=Severity.WARNING, message=message)


def _error(code: IssueCode, message: str) -> Issue:
    return Issue(code=code, severity=Severity.ERROR, message=message)


def _definition_matches(ctx: AppContext, tree: Any, dialect: Dialect) -> list[Issue]:
    """Info issues for usable definitions whose original query is `tree` (AST-equal)."""
    states = _library.usable_states(team_mode(ctx))
    hints: list[Issue] = []
    for latest in ctx.store.list_definitions():
        definition = _library.newest_in_states(ctx, latest, states)
        if definition is None or definition.source_dialect is not dialect:
            continue
        if definition.composition is not None:  # composed: no original query of its own
            continue
        try:
            original = parse(_library.original_sql(definition), dialect)
        except CteforgeError:
            continue
        if original == tree:
            hints.append(
                Issue(
                    code=IssueCode.MATCHES_DEFINITION,
                    severity=Severity.INFO,
                    message=(
                        f"This SQL is the original query of definition '{definition.id}' "
                        f"v{definition.version} ({definition.state}). Call compile_and_run "
                        f'with uses={{"{definition.id}": {{"id": "{definition.id}"}}}} '
                        "instead: it keeps that definition's trust label and lets you change "
                        "its variables by value."
                    ),
                )
            )
    return hints


_RUN_TOOLS = {
    AuditKind.COMPILE_AND_RUN: Tool.COMPILE_AND_RUN,
    AuditKind.CHECK_AND_RUN: Tool.CHECK_AND_RUN,
}


def _require(ctx: AppContext, user: User, kind: AuditKind, question: str | None) -> None:
    with _audit.audit_failures(ctx, user, kind, question=question):
        require(user, _RUN_TOOLS[kind])


# ── Result size ──────────────────────────────────────────────────────────────────────

# Rows handed back per call are capped by size as well as count: max_rows bounds the count,
# but a row can be arbitrarily wide (DESIGN §14).
MAX_RESULT_BYTES = 8 * 1024 * 1024


def _cap_bytes(rows: list[list[Any]], budget: int) -> tuple[list[list[Any]], bool]:
    """The leading rows whose JSON fits in `budget` bytes, and whether any were dropped."""
    used = 0
    for n, row in enumerate(rows):
        used += len(json.dumps(row, separators=(",", ":"), default=str).encode("utf-8"))
        if used > budget:
            return rows[:n], True
    return rows, False


# ── Join map ─────────────────────────────────────────────────────────────────────────


def _known_edges(ctx: AppContext) -> list[JoinEdge]:
    """Join-map edges that count as known (ADR 0026; `_library.known_edges`)."""
    return _library.known_edges(ctx)


def _unconnected(tables: list[str], known_pairs: list[tuple[str, str]]) -> list[list[str]]:
    """Groups of `tables` connected by `known_pairs` (union-find); one group = connected."""
    parent = {t: t for t in tables}

    def find(t: str) -> str:
        while parent[t] != t:
            parent[t] = parent[parent[t]]
            t = parent[t]
        return t

    def key(name: str) -> str | None:
        return next((t for t in tables if _library.same_table(t, name)), None)

    for a, b in known_pairs:
        ka, kb = key(a), key(b)
        if ka is not None and kb is not None:
            parent[find(ka)] = find(kb)
    groups: dict[str, list[str]] = {}
    for t in tables:
        groups.setdefault(find(t), []).append(t)
    return list(groups.values())


def _base_join(join: JoinInfo, edges: list[JoinEdge]) -> tuple[str, str] | tuple[()] | None:
    """How a join in exploratory SQL relates to the join map, judged on the BASE tables
    behind its sides (JoinInfo `*_base_table` / `base_keys`: a CTE or derived table whose
    key columns pass through unchanged from one base table), as join mining does:
    - the base tables' pair when an edge matches it (known);
    - `()` when both sides lead to the same column(s) of one base table (two CTEs over
      one table aligned on its key: no relationship between tables, nothing to connect);
    - None when it is unknown (no edge, or a side whose keys are computed)."""
    left, right = join.left_base_table, join.right_base_table
    if left is not None and right is not None and join.base_keys:
        if _library.matching_edge(left, right, join.base_keys, edges) is not None:
            return (left, right)
        through_cte = (join.left_table, join.right_table) != (left, right)
        same_columns = all(a.lower() == b.lower() for a, b in join.base_keys)
        if through_cte and _library.same_table(left, right) and same_columns:
            return ()
        return None
    if _library.matching_edge(join.left_table, join.right_table, join.keys, edges) is not None:
        return (join.left_table, join.right_table)
    return None


def _through(join: JoinInfo) -> str:
    """' (base tables a ↔ b on ...)' when a side is a CTE/derived table, else ''."""
    left, right = join.left_base_table, join.right_base_table
    if left is None or right is None or not join.base_keys:
        if (join.left_base_table, join.right_base_table) == (join.left_table, join.right_table):
            return ""
        return " (a side's key is computed in a CTE, so its base table is unknown)"
    if (left, right) == (join.left_table, join.right_table):
        return ""
    return f" (base tables '{left}' and '{right}' on {_describe_keys(join.base_keys)})"


def _describe_keys(keys: Iterable[tuple[str, str]]) -> str:
    return _library.describe_keys(keys)


# ── Loading ──────────────────────────────────────────────────────────────────────────


def _ref(alias: str, spec: Any) -> DefinitionRef:
    if isinstance(spec, DefinitionRef):
        return spec
    if isinstance(spec, str):
        spec = {"id": spec}
    try:
        return DefinitionRef.model_validate(spec)
    except ValidationError:
        raise RenderError(
            f"uses['{alias}'] must be {{'id': <definition id>, 'version': <int, optional>}}"
        ) from None


def _load(ctx: AppContext, ref: DefinitionRef, mode: TeamMode) -> Definition:
    if ref.version is not None:
        return _library.get_definition(ctx, ref.id, ref.version)
    latest = _library.get_definition(ctx, ref.id)
    found = _library.newest_in_states(ctx, latest, _library.usable_states(mode))
    return latest if found is None else found


@dataclass
class _Request:
    refs: dict[str, DefinitionRef]
    values: dict[str, dict[str, Any]]
    select: str | None
    blocking: list[Issue] = field(default_factory=list)


def _request(
    uses: Mapping[str, Any] | None,
    values: Mapping[str, Any],
    select: str | None,
) -> _Request:
    if not uses:
        raise RenderError("`uses` must name at least one definition (alias → {id, version?}).")
    for alias, given in values.items():
        if not isinstance(given, Mapping):
            raise RenderError(f"values['{alias}'] must be an object of variable → value")
    request = _Request(
        refs={alias: _ref(alias, spec) for alias, spec in uses.items()},
        values={alias: dict(v) for alias, v in values.items()},
        select=select,
    )
    unknown = sorted(set(request.values) - set(request.refs))
    if unknown:
        raise RenderError(
            f"Values given for unknown alias(es) {unknown}; aliases are {sorted(request.refs)}."
        )
    return request


def _target(
    ctx: AppContext,
    definitions: dict[str, Definition],
    profile: str | None,
    dialect: Dialect | None,
    run: bool,
) -> tuple[str | None, Dialect, str | None]:
    """(profile name or None, target dialect, why EXPLAIN is skipped or None). See the
    module docstring."""
    named = sorted({d.profile for d in definitions.values() if d.profile})
    if profile is None and len(named) > 1:
        raise PolicyError(f"The definitions run on different profiles {named}; pass `profile`.")
    wanted = profile or (named[0] if named else None)
    name: str | None
    try:
        name = ctx.warehouses.resolve_name(wanted)
    except PolicyError:
        if run:
            raise
        name = None
    unchecked: str | None = None
    if name is not None:
        target = ctx.warehouses.summary(name).dialect
        if dialect is not None and dialect is not target:
            if run:
                raise PolicyError(
                    f"Requested dialect {dialect} but profile '{name}' is {target}; "
                    "SQL can only run in the warehouse's own dialect. Pass run=false to "
                    f"only compile it for {dialect} (not checked with EXPLAIN)."
                )
            unchecked = (
                f"Compiled for {dialect}, but profile '{name}' is {target}: the SQL was not "
                "checked with EXPLAIN and cannot run there."
            )
            name, target = None, dialect
    elif dialect is not None:
        target = dialect
    else:
        sources = sorted({d.source_dialect for d in definitions.values()})
        if len(sources) != 1:
            raise PolicyError(f"The definitions have different dialects {sources}; pass `dialect`.")
        target = sources[0]
    return name, target, unchecked


# ── Execution (shared) ───────────────────────────────────────────────────────────────


def _refused(
    ctx: AppContext,
    user: User,
    kind: AuditKind,
    *,
    sql: str,
    dialect: Dialect,
    label: TrustLabel,
    reasons: list[str],
    issues: list[Issue],
    definitions: list[str],
    question: str | None,
    values: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
    used: list[UsedDefinition] | None = None,
) -> RunOutcome:
    _audit.record(
        ctx,
        user,
        kind,
        sql=sql or None,
        outcome=AuditOutcome.REJECTED,
        question=question,
        definitions=definitions,
        values=values or {},
        trust_label=label,
        extra=extra or {},
    )
    return RunOutcome(
        sql=sql,
        dialect=dialect,
        label=label,
        reasons=reasons,
        usable=False,
        issues=issues,
        ran=False,
        definitions=definitions,
        used=used or [],
        values_used=json_safe(values or {}),
    )


def _execute(
    ctx: AppContext,
    user: User,
    kind: AuditKind,
    *,
    sql: str,
    dialect: Dialect,
    profile: str | None,
    trust: TrustResult,
    issues: list[Issue],
    definitions: list[str],
    run: bool,
    limit: int,
    question: str | None,
    values: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
    used: list[UsedDefinition] | None = None,
    unchecked: str | None = None,
) -> RunOutcome:
    """Tag, EXPLAIN, run (if `run`), audit (fail closed), and build the outcome.
    `profile=None` compiles only; `unchecked` then says why (default: no profile)."""
    compile_id = new_compile_id()
    tagged = tag_sql(sql, sql_tag(definitions, compile_id))
    issues = list(issues)
    extra = {**(extra or {}), "profile": profile, "run": run}
    common: dict[str, Any] = {
        "sql": tagged,
        "dialect": dialect,
        "label": trust.label,
        "reasons": trust.reasons,
        "usable": True,
        "compile_id": compile_id,
        "definitions": definitions,
        "used": used or [],
        "values_used": json_safe(values or {}),
    }
    audit_fields: dict[str, Any] = {
        "question": question,
        "definitions": definitions,
        "values": values or {},
        "trust_label": trust.label,
        "compile_id": compile_id,
    }
    if profile is None:
        issues.append(
            _warning(
                IssueCode.CHECKS_INCOMPLETE,
                unchecked
                or "No warehouse profile is configured: the SQL was compiled but not "
                "checked with EXPLAIN.",
            )
        )
        _audit.record(ctx, user, kind, sql=tagged, extra=extra, **audit_fields)
        return RunOutcome(**common, issues=issues, ran=False)

    max_rows = ctx.warehouses.profile(profile).max_rows
    effective = max(1, min(limit, max_rows))
    started = time.perf_counter()
    try:
        warehouse = ctx.warehouses.get(profile)
        warehouse.explain(tagged)
        result = warehouse.run(tagged, effective) if run else None
    except WarehouseError as exc:
        duration = int((time.perf_counter() - started) * 1000)
        issues.append(
            _error(IssueCode.RUN_FAILED, f"The warehouse could not compile or run it: {exc}")
        )
        failed = {
            **common,
            "usable": False,  # Nothing ran: there is no answer, not an empty one.
            "reasons": [
                *trust.reasons,
                "Not usable: the warehouse could not compile or run the query (see the "
                "run_failed issue); no rows were produced.",
            ],
        }
        _audit.record(
            ctx,
            user,
            kind,
            sql=tagged,
            error=exc,
            duration_ms=duration,
            extra=extra,
            **audit_fields,
        )
        return RunOutcome(**failed, issues=issues, ran=False, duration_ms=duration)
    duration = int((time.perf_counter() - started) * 1000)
    if result is None:
        _audit.record(
            ctx, user, kind, sql=tagged, duration_ms=duration, extra=extra, **audit_fields
        )
        return RunOutcome(**common, issues=issues, ran=False, duration_ms=duration)
    if result.truncated:
        issues.append(
            _warning(
                IssueCode.ROW_LIMIT_REACHED,
                f"Only the first {effective} rows are returned; more exist (profile maximum "
                f"{max_rows}). Aggregate or filter further.",
            )
        )
    rows, capped = _cap_bytes(json_rows(result.rows), MAX_RESULT_BYTES)
    if capped:
        issues.append(
            _warning(
                IssueCode.ROW_LIMIT_REACHED,
                f"Only the first {len(rows)} rows are returned: the result is larger than "
                f"{MAX_RESULT_BYTES // (1024 * 1024)} MiB. Select fewer or narrower columns.",
            )
        )
    truncated = result.truncated or capped
    # Fail closed: if this event cannot be written, no rows are handed back.
    _audit.record(
        ctx,
        user,
        kind,
        sql=tagged,
        row_count=len(rows),
        duration_ms=duration,
        extra={**extra, "truncated": truncated},
        **audit_fields,
    )
    return RunOutcome(
        **common,
        issues=issues,
        columns=list(result.columns),
        rows=rows,
        row_count=len(rows),
        truncated=truncated,
        ran=True,
        duration_ms=duration,
    )


# ── compile_and_run ──────────────────────────────────────────────────────────────────


def compile_and_run(
    ctx: AppContext,
    user: User,
    *,
    uses: Mapping[str, Any] | None = None,
    values: Mapping[str, Mapping[str, Any]] | None = None,
    select: str | None = None,
    dialect: Dialect | str | None = None,
    profile: str | None = None,
    run: bool = True,
    limit: int = 100,
    question: str | None = None,
) -> RunOutcome:
    """Render definitions by reference (+ optional final SELECT), label, check, run.

    `uses`: alias → {"id", "version"?} (or a DefinitionRef, or just the id string); a
    composed definition is used like any other (ADR 0026).
    `values`: alias → variable → value in each variable's input format (a composed
    definition's variables are `<part alias>.<var>`). Raises
    NotFoundError, RenderError, VariableValueError, SqlParseError, PolicyError for input
    the call cannot work with; trust refusals and warehouse failures come back as a
    RunOutcome with issues.
    """
    kind = AuditKind.COMPILE_AND_RUN
    _require(ctx, user, kind, question)
    given = dict(values or {})
    try:
        target = None if dialect is None else _library.parse_dialect(dialect)
        return _compile_and_run(
            ctx, user, uses, given, select, target, profile, run, limit, question
        )
    except AuditError:
        raise  # The audit write itself failed; nothing more can be recorded.
    except CteforgeError as exc:
        # Fail closed: an input error that cannot be recorded surfaces as AuditError.
        _audit.record(
            ctx,
            user,
            kind,
            error=exc,
            outcome=_audit.failure_outcome(exc),
            question=question,
            values=json_safe(given),
            extra={"aliases": sorted(uses or {})},
        )
        raise


def _compile_and_run(
    ctx: AppContext,
    user: User,
    uses: Mapping[str, Any] | None,
    values: dict[str, Any],
    select: str | None,
    dialect: Dialect | None,
    profile: str | None,
    run: bool,
    limit: int,
    question: str | None,
) -> RunOutcome:
    kind = AuditKind.COMPILE_AND_RUN
    mode = team_mode(ctx)
    request = _request(uses, values, select)
    definitions = {alias: _load(ctx, ref, mode) for alias, ref in request.refs.items()}
    profile_name, target, unchecked = _target(ctx, definitions, profile, dialect, run)
    extra: dict[str, Any] = {"aliases": sorted(definitions)}

    parts: list[Part] = []
    issues: list[Issue] = []
    plain: dict[str, Any] = {}
    compiled: list[_compose.Compiled] = []
    for alias, definition in definitions.items():
        result = _compose.compile_definition(
            ctx, definition, request.values.get(alias, {}), target, mode=mode, prefix=f"{alias}."
        )
        compiled.append(result)
        try:
            parts.append(Part(alias=alias, definition=definition, sql=result.sql))
        except ValidationError:
            raise RenderError(f"Alias '{alias}' must be lower_snake_case.") from None
        issues += result.issues
        plain[alias] = result.values
    used_defs = list({f"{d.id}@{d.version}": d for c in compiled for d in c.used}.values())
    used = [f"{d.id}@{d.version}" for d in used_defs]
    used_cards = [used_definition(d) for d in used_defs]

    final = request.select if request.select and request.select.strip() else None
    if final is not None:
        try:
            ensure_query(parse(final, target), "final SELECT")
        except RenderError as exc:
            return _refused(
                ctx,
                user,
                kind,
                sql="",
                dialect=target,
                label=TrustLabel.EXPLORATORY,
                reasons=["Refused: the final SELECT is not a single read-only query."],
                issues=[*issues, _error(IssueCode.NOT_READ_ONLY, str(exc))],
                definitions=used,
                question=question,
                values=plain,
                extra=extra,
                used=used_cards,
            )

    composed = compose(parts, final, target)
    # ADR 0027: a final SELECT over raw tables is exploratory SQL and also needs
    # check_and_run — the agent's own select, and the stored final SELECT of any composed
    # definition used (verified or not, at any depth; ADR 0026). Before the warehouse.
    reads_raw = [t for c in compiled for t in c.reads_raw]
    if final is not None:
        reads_raw += composed.raw_tables
    if reads_raw:
        _compose.require_exploratory(user, reads_raw)
    issues += composed.issues
    joins_known = all(c.joins_known for c in compiled)
    raw_tables = [t for c in compiled for t in c.raw_tables]
    if final is not None:
        edges = _known_edges(ctx) if (composed.joins or composed.raw_tables) else []
        select_known, join_issues = _library.final_select_joins(composed, set(definitions), edges)
        joins_known = joins_known and select_known
        issues += join_issues
        raw_tables += list(composed.raw_tables)

    raw_tables = list(dict.fromkeys(raw_tables))
    trust = assess(
        TrustInputs(
            definition_states=[d.state for c in compiled for d in c.sql_definitions],
            composed_states=[d.state for c in compiled for d in c.composed_definitions],
            # A final SELECT that reads base tables is agent-written logic over raw data,
            # not only a combination of the definitions (DESIGN §8, §17).
            raw_sql=bool(raw_tables),
            has_select=final is not None,
            joins_known=joins_known,
            issues=issues,
            mode=mode,
        )
    )
    if raw_tables:
        trust = trust.model_copy(
            update={
                "reasons": [
                    *trust.reasons,
                    f"A final SELECT reads raw table(s) {raw_tables} directly, not only "
                    "the definitions.",
                ]
            }
        )
    blocking = [*trust.blocking, *request.blocking, *(b for c in compiled for b in c.blocking)]
    if blocking:
        return _refused(
            ctx,
            user,
            kind,
            sql=composed.sql,
            dialect=target,
            label=trust.label,
            reasons=trust.reasons,
            issues=[*issues, *(b for b in blocking if b not in issues)],
            definitions=used,
            question=question,
            values=plain,
            extra=extra,
            used=used_cards,
        )
    return _execute(
        ctx,
        user,
        kind,
        sql=composed.sql,
        dialect=target,
        profile=profile_name,
        trust=trust,
        issues=issues,
        definitions=used,
        run=run,
        limit=limit,
        question=question,
        values=plain,
        extra=extra,
        used=used_cards,
        unchecked=unchecked,
    )


# ── check_and_run ────────────────────────────────────────────────────────────────────


def check_and_run(
    ctx: AppContext,
    user: User,
    sql: str,
    *,
    profile: str | None = None,
    run: bool = True,
    limit: int = 100,
    question: str | None = None,
) -> RunOutcome:
    """Exploratory raw SQL, labeled `new_logic_on_known_joins` or `exploratory`.

    Raises SqlParseError (unparseable or several statements), NotFoundError / PolicyError
    (profile). Not-read-only SQL and strict-mode refusals come back as a refused outcome.
    """
    kind = AuditKind.CHECK_AND_RUN
    _require(ctx, user, kind, question)
    try:
        name = ctx.warehouses.resolve_name(profile)
        dialect = ctx.warehouses.summary(name).dialect
        tree = parse(sql, dialect)
    except CteforgeError as exc:
        _audit.record(
            ctx,
            user,
            kind,
            sql=sql,
            error=exc,
            # SQL that does not parse (or holds several statements) is refused input.
            outcome=(
                AuditOutcome.REJECTED
                if isinstance(exc, SqlParseError)
                else _audit.failure_outcome(exc)
            ),
            question=question,
            extra={"profile": profile},
        )
        raise
    extra: dict[str, Any] = {"profile": name}
    try:
        ensure_query(tree, "query")
    except RenderError as exc:
        return _refused(
            ctx,
            user,
            kind,
            sql="",
            dialect=dialect,
            label=TrustLabel.EXPLORATORY,
            reasons=["Refused: only a single read-only SELECT can run."],
            issues=[_error(IssueCode.NOT_READ_ONLY, str(exc))],
            definitions=[],
            question=question,
            extra=extra,
        )
    analysis = analyze(sql, dialect)
    joins, tables = analysis.joins, list(dict.fromkeys(analysis.tables))
    edges = _known_edges(ctx) if (joins or len(tables) > 1) else []
    issues: list[Issue] = []
    known_pairs: list[tuple[str, str]] = []
    for j in joins:
        pair = _base_join(j, edges)
        if pair is None:
            issues.append(
                _warning(
                    IssueCode.UNKNOWN_JOIN,
                    f"Join between '{j.left_table}' and '{j.right_table}' "
                    f"({_describe_keys(j.keys)}) is not in the join map{_through(j)}.",
                )
            )
        elif pair:
            known_pairs.append(pair)
    groups = _unconnected(tables, known_pairs)
    if len(groups) > 1:
        issues.append(
            _warning(
                IssueCode.UNKNOWN_JOIN,
                f"The query reads tables that no known join connects ({groups}); they may be "
                "cross-joined (comma, CROSS, LATERAL or a subquery). Join them on join-map "
                "keys, or propose the join.",
            )
        )
    trust = assess(
        TrustInputs(
            definition_states=[],
            raw_sql=True,
            joins_known=not issues,
            issues=issues,
            mode=team_mode(ctx),
        )
    )
    issues = [*issues, *_definition_matches(ctx, tree, dialect)]  # hints; label unchanged
    if trust.blocking:
        return _refused(
            ctx,
            user,
            kind,
            sql=sql,
            dialect=dialect,
            label=trust.label,
            reasons=trust.reasons,
            issues=[*issues, *trust.blocking],
            definitions=[],
            question=question,
            extra=extra,
        )
    return _execute(
        ctx,
        user,
        kind,
        sql=sql,
        dialect=dialect,
        profile=name,
        trust=trust,
        issues=issues,
        definitions=[],
        run=run,
        limit=limit,
        question=question,
        extra=extra,
    )
