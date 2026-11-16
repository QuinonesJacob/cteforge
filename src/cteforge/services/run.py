"""Use-cases: compile_and_run (definitions by reference) and check_and_run (exploratory SQL).

DESIGN §6, §8, §11, §17; ADR 0005, 0007, 0008, 0013. Both need Action.RUN (viewer).

compile_and_run flow: resolve `uses` (or a recipe) → load each definition → pick the
warehouse profile and target dialect → render each definition (templating) → compose with
the optional final SELECT → check the final SELECT's joins against the join map → trust
label (trust.assess) → refuse if not usable → tag the SQL (ADR 0008) → EXPLAIN → run →
audit → RunOutcome.

Choices made here (documented contract):
- Versions: a pinned version is used as is (a pinned draft is refused by trust). A floating
  reference takes the newest version usable in the team mode (`_library.usable_states`:
  verified, or in open mode also llm_annotated); with none, the latest version (trust then
  says why it is refused).
- Profile: `profile`, else the definitions' own `profile` (all must agree), else the
  registry default. The target dialect is the profile's dialect; a `dialect` argument that
  differs is refused (the SQL could not run there). With `run=False` and no warehouse
  configured at all, SQL is compiled for `dialect` (or the recipe's / the definitions'
  common source dialect) without EXPLAIN, with a `checks_incomplete` warning.
- A recipe's own final SELECT is used; passing `select` with `recipe_id` is refused, as is a
  recipe whose dialect differs from the target. Values given with a recipe override the
  recipe's values per variable. A draft recipe is refused (`draft_not_usable`).
- Final-SELECT joins: alias↔alias joins are known when compose says so (declared
  join_keys). A join with a raw-table side is known only when a JoinEdge in the store
  matches it (`_library.matching_edge`: same two tables, case-insensitive, an unqualified
  name matching a qualified one by its last part; exactly the same key pairs, in either
  orientation). A raw table read outside
  any known join also makes joins unknown. Unknown → `unknown_join` warning, and the trust
  label falls to `exploratory`. Join-map edges in state draft never count as known.
- Raw tables in a final SELECT (DESIGN §8, §17): reading a base table directly is new
  logic, not "built from verified parts", even when every join is known. The label is then
  assessed as raw SQL (`new_logic_on_known_joins` at best, capped further by the
  definitions' states), so strict mode refuses it.
- Provenance: `RunOutcome.used` lists each definition version with its state and, when
  verified, `verified_by` / `verified_at` (DESIGN §8).
- Row limit: `limit` clamped to 1..profile.max_rows; the warehouse enforces it again.
  `row_limit_reached` warning when more rows existed.
- Audit (fail closed for executed runs: the event is written before rows are returned):
  definitions as id@version, values actually used per alias, label, compile_id, SHA-256 of
  the tagged SQL, row count, duration. Never SQL text or rows. Refusals → outcome rejected;
  input errors raised to the caller → outcome per `_audit.failure_outcome` (PolicyError
  rejected, others error); warehouse failures → outcome error, class name only; the
  caller still gets the (connector-scrubbed) warehouse message in a `run_failed` issue.

check_and_run: raw SQL on a profile (default profile unless named). One read-only
SELECT/UNION only (compose.checks.ensure_query; else refused with `not_read_only`; SQL that
does not parse, including several statements, raises SqlParseError). Its joins
(parsing.analyze) are compared with the join map as above, and every base table it reads
must be connected to the others through known joins: tables read with no known join
between them (a comma/CROSS join, LATERAL, a subquery in FROM or WHERE, a UNION of
different tables) make joins unknown. → `new_logic_on_known_joins` or `exploratory`
(trust.assess, raw_sql=True). Tagged `cteforge: exploratory`.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from cteforge.audit.tag import new_compile_id, sql_tag, tag_sql
from cteforge.compose.assemble import ComposedQuery, Part, compose
from cteforge.compose.checks import ensure_query
from cteforge.domain.audit import AuditKind, AuditOutcome
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import (
    AuditError,
    CteforgeError,
    NotFoundError,
    PolicyError,
    RenderError,
    SqlParseError,
    WarehouseError,
)
from cteforge.domain.issues import Issue, IssueCode, Severity
from cteforge.domain.models import (
    Definition,
    DefinitionRef,
    DefinitionState,
    JoinEdge,
    TeamMode,
    TrustLabel,
)
from cteforge.domain.users import User
from cteforge.parsing.analyze import analyze, parse
from cteforge.services import _audit, _library
from cteforge.services.auth import require
from cteforge.services.cards import (
    RunOutcome,
    UsedDefinition,
    json_rows,
    json_safe,
    used_definition,
)
from cteforge.services.context import AppContext, team_mode
from cteforge.templating.render import render
from cteforge.trust.approval import Action
from cteforge.trust.labels import TrustInputs, TrustResult, assess


def _warning(code: IssueCode, message: str) -> Issue:
    return Issue(code=code, severity=Severity.WARNING, message=message)


def _error(code: IssueCode, message: str) -> Issue:
    return Issue(code=code, severity=Severity.ERROR, message=message)


def _require(ctx: AppContext, user: User, kind: AuditKind, question: str | None) -> None:
    with _audit.audit_failures(ctx, user, kind, question=question):
        require(user, Action.RUN)


# ── Join map ─────────────────────────────────────────────────────────────────────────


def _known_edges(ctx: AppContext) -> list[JoinEdge]:
    """Join-map edges that count as known: every edge except drafts."""
    return [e for e in ctx.store.list_joins() if e.state is not DefinitionState.DRAFT]


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


def _describe_keys(keys: Iterable[tuple[str, str]]) -> str:
    return " AND ".join(f"{a} = {b}" for a, b in keys) or "no equality keys"


def _final_select_joins(
    composed: ComposedQuery, aliases: set[str], edges: list[JoinEdge]
) -> tuple[bool, list[Issue]]:
    """(joins_known, unknown_join issues for raw-table joins). Alias↔alias joins were
    already checked by compose (it reports their unknown_join issues itself)."""
    known = True
    issues: list[Issue] = []
    covered: set[str] = set()
    for join in composed.joins:
        left, right = join.left.lower(), join.right.lower()
        if left in aliases and right in aliases:
            known = known and join.known
            continue
        if _library.matching_edge(join.left, join.right, join.keys, edges) is None:
            known = False
            issues.append(
                _warning(
                    IssueCode.UNKNOWN_JOIN,
                    f"Join between '{join.left}' and '{join.right}' "
                    f"({_describe_keys(join.keys)}) is not in the join map. Check the "
                    "relationship before trusting the result, or propose the join.",
                )
            )
        else:
            covered |= {left, right}
    loose = [t for t in composed.raw_tables if not any(_library.same_table(t, c) for c in covered)]
    if loose:
        known = False
        issues.append(
            _warning(
                IssueCode.UNKNOWN_JOIN,
                f"The final SELECT reads table(s) {loose} outside any known join; the "
                "result is not built only from the definitions.",
            )
        )
    return known, issues


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
        pinned = ctx.store.get_definition(ref.id, ref.version)
        if pinned is None:
            raise NotFoundError(f"Definition '{ref.id}' version {ref.version} does not exist")
        return pinned
    latest = ctx.store.get_definition(ref.id)
    if latest is None:
        raise NotFoundError(f"Definition '{ref.id}' does not exist")
    found = _library.newest_in_states(ctx, latest, _library.usable_states(mode))
    return latest if found is None else found


@dataclass
class _Request:
    refs: dict[str, DefinitionRef]
    values: dict[str, dict[str, Any]]
    select: str | None
    select_dialect: Dialect | None = None
    recipe: str | None = None
    blocking: list[Issue] = field(default_factory=list)


def _request(
    ctx: AppContext,
    uses: Mapping[str, Any] | None,
    recipe_id: str | None,
    values: Mapping[str, Any],
    select: str | None,
) -> _Request:
    if (uses is None) == (recipe_id is None):
        raise PolicyError("Give either `uses` (alias → definition) or `recipe_id`, not both.")
    for alias, given in values.items():
        if not isinstance(given, Mapping):
            raise RenderError(f"values['{alias}'] must be an object of variable → value")
    if recipe_id is not None:
        if select is not None:
            raise PolicyError("A recipe carries its own final SELECT; do not pass `select`.")
        recipe = ctx.store.get_recipe(recipe_id)
        if recipe is None:
            raise NotFoundError(f"Recipe '{recipe_id}' does not exist")
        merged = {alias: dict(v) for alias, v in recipe.values.items()}
        for alias, given in values.items():
            merged.setdefault(alias, {}).update(given)
        request = _Request(
            refs=dict(recipe.uses),
            values=merged,
            select=recipe.select,
            select_dialect=recipe.dialect,
            recipe=f"{recipe.id}@{recipe.version}",
        )
        if recipe.state is DefinitionState.DRAFT:
            request.blocking.append(
                _error(
                    IssueCode.DRAFT_NOT_USABLE,
                    f"Recipe '{recipe.id}' is a draft and can't be used until it is approved.",
                )
            )
    else:
        assert uses is not None
        if not uses:
            raise RenderError("`uses` must name at least one definition.")
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
    select_dialect: Dialect | None,
    run: bool,
) -> tuple[str | None, Dialect]:
    """(profile name or None, target dialect). See the module docstring."""
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
    if name is not None:
        target = ctx.warehouses.summary(name).dialect
        if dialect is not None and dialect is not target:
            raise PolicyError(
                f"Requested dialect {dialect} but profile '{name}' is {target}; "
                "SQL can only run in the warehouse's own dialect."
            )
    elif dialect is not None:
        target = dialect
    elif select_dialect is not None:
        target = select_dialect
    else:
        sources = sorted({d.source_dialect for d in definitions.values()})
        if len(sources) != 1:
            raise PolicyError(f"The definitions have different dialects {sources}; pass `dialect`.")
        target = sources[0]
    if select_dialect is not None and select_dialect is not target:
        raise PolicyError(
            f"The recipe's SELECT is written in {select_dialect} but the target is {target}."
        )
    return name, target


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
) -> RunOutcome:
    """Tag, EXPLAIN, run (if `run`), audit (fail closed), and build the outcome."""
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
                "No warehouse profile is configured: the SQL was compiled but not checked "
                "with EXPLAIN.",
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
        return RunOutcome(**common, issues=issues, ran=False, duration_ms=duration)
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
    rows = json_rows(result.rows)
    # Fail closed: if this event cannot be written, no rows are handed back.
    _audit.record(
        ctx,
        user,
        kind,
        sql=tagged,
        row_count=len(rows),
        duration_ms=duration,
        extra={**extra, "truncated": result.truncated},
        **audit_fields,
    )
    return RunOutcome(
        **common,
        issues=issues,
        columns=list(result.columns),
        rows=rows,
        row_count=len(rows),
        truncated=result.truncated,
        ran=True,
        duration_ms=duration,
    )


# ── compile_and_run ──────────────────────────────────────────────────────────────────


def compile_and_run(
    ctx: AppContext,
    user: User,
    *,
    uses: Mapping[str, Any] | None = None,
    recipe_id: str | None = None,
    values: Mapping[str, Mapping[str, Any]] | None = None,
    select: str | None = None,
    dialect: Dialect | str | None = None,
    profile: str | None = None,
    run: bool = True,
    limit: int = 100,
    question: str | None = None,
) -> RunOutcome:
    """Render definitions by reference (+ optional final SELECT), label, check, run.

    `uses`: alias → {"id", "version"?} (or a DefinitionRef, or just the id string).
    `values`: alias → variable → value in each variable's input format. Raises
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
            ctx, user, uses, recipe_id, given, select, target, profile, run, limit, question
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
            extra={"recipe": recipe_id, "aliases": sorted(uses or {})},
        )
        raise


def _compile_and_run(
    ctx: AppContext,
    user: User,
    uses: Mapping[str, Any] | None,
    recipe_id: str | None,
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
    request = _request(ctx, uses, recipe_id, values, select)
    definitions = {alias: _load(ctx, ref, mode) for alias, ref in request.refs.items()}
    used = list(dict.fromkeys(f"{d.id}@{d.version}" for d in definitions.values()))
    by_ref = {f"{d.id}@{d.version}": used_definition(d) for d in definitions.values()}
    used_cards = list(by_ref.values())
    profile_name, target = _target(ctx, definitions, profile, dialect, request.select_dialect, run)
    extra: dict[str, Any] = {"recipe": request.recipe, "aliases": sorted(definitions)}

    parts: list[Part] = []
    issues: list[Issue] = []
    plain: dict[str, Any] = {}
    for alias, definition in definitions.items():
        rendered = render(definition, request.values.get(alias, {}), target)
        try:
            parts.append(Part(alias=alias, definition=definition, sql=rendered.sql))
        except ValidationError:
            raise RenderError(f"Alias '{alias}' must be lower_snake_case.") from None
        issues += rendered.issues
        plain[alias] = rendered.values

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
    issues += composed.issues
    joins_known = True
    if final is not None:
        edges = _known_edges(ctx) if (composed.joins or composed.raw_tables) else []
        joins_known, join_issues = _final_select_joins(composed, set(definitions), edges)
        issues += join_issues

    raw_tables = list(composed.raw_tables) if final is not None else []
    trust = assess(
        TrustInputs(
            definition_states=[d.state for d in definitions.values()],
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
                    f"The final SELECT reads raw table(s) {raw_tables} directly, not only "
                    "the definitions.",
                ]
            }
        )
    blocking = [*trust.blocking, *request.blocking]
    if blocking:
        return _refused(
            ctx,
            user,
            kind,
            sql=composed.sql,
            dialect=target,
            label=trust.label,
            reasons=trust.reasons,
            issues=[*issues, *blocking],
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
        if _library.matching_edge(j.left_table, j.right_table, j.keys, edges) is None:
            issues.append(
                _warning(
                    IssueCode.UNKNOWN_JOIN,
                    f"Join between '{j.left_table}' and '{j.right_table}' "
                    f"({_describe_keys(j.keys)}) is not in the join map.",
                )
            )
        else:
            known_pairs.append((j.left_table, j.right_table))
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
