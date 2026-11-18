"""Composed definitions (ADR 0026, DESIGN §17) — support module, not a use-case.

No permission checks and no audit of its own: callers `require` and record.

A composed definition's body is a `Composition` (parts by alias, `fixed` values) and a
final SELECT (its `template`, "" = one part used as is). Everything here works on stored
definitions through the store; SQL is built only by templating (render) and compose.

- Parts resolve like `compile_and_run` uses (`resolve`): a pinned part is that version;
  a floating part is the newest version usable in the team mode (`_library.usable_states`),
  or — when none is — its latest version plus a `part_not_usable` error naming the part
  (the composed definition is then unusable). A pinned part with newer versions gets a
  `part_superseded` info issue.
- Values (`split_values`): a composed definition's variables are its parts' open
  variables, named `<alias>.<var>` (nested: `<alias>.<sub>.<var>`), plus its final
  SELECT's own. A caller may also pass `{"<alias>": {"<var>": value}}`. A part variable set
  by `fixed` is not open; giving another value for it is an override (`fixed_override`
  warning, lowers the label like overriding a fixed variable).
- `compile_definition` renders a definition (SQL or composed, recursively: nesting is
  allowed, cycles raise PolicyError `composition_cycle`, depth ≤ MAX_DEPTH) into one query
  in the target dialect and collects what trust needs: the SQL definitions used, the
  composed definitions used (their own states), and — for each composed definition no
  person has verified — whether its final SELECT's joins are known and which raw tables
  it reads. A VERIFIED composed definition's final SELECT and choice of parts were reviewed
  as a whole, so its alias joins are not re-judged (its unknown_join issues are dropped).
- `cycle_issue`: the cycle check proposals run before anything is stored (and again
  when one is applied).
- `checked_composition`: the proposal checks of a composed definition (DESIGN §7 adapted):
  the final SELECT is one read-only SELECT (`not_read_only`, PolicyError, first), its
  annotations become a template whose original values give the same query back; every
  part resolves (`not_found`), `fixed` names open variables of its part
  (`composition_invalid`), no cycle (`composition_cycle`), the whole compiles (render +
  compose) and EXPLAINs on the warehouse; with annotations, the compiled query with the
  original SELECT and with the template are run and compared. Column types of the final
  SELECT come from its parts, not from warehouse tables, so a variable's type is not
  checked against them (`type_unknown` info; the results comparison covers the original
  values). A part with no version usable in the team mode is a `part_not_usable` WARNING
  here (approvable, never verifiable; running refuses it until the part is usable). State:
  llm_annotated when nothing failed and the warehouse checks ran, else draft.
- `open_variables`, `composition_card`: what cards show (parts, fixed/open variables, the
  compiled SQL with original values).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from cteforge.compose.assemble import Part, compose
from cteforge.compose.checks import ensure_query
from cteforge.domain.access import Tool
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import (
    CodecError,
    CteforgeError,
    NotFoundError,
    PermissionDeniedError,
    PolicyError,
    RenderError,
    WarehouseError,
)
from cteforge.domain.issues import Issue, IssueCode, Severity, has_errors
from cteforge.domain.models import (
    Annotation,
    Composition,
    Definition,
    DefinitionRef,
    DefinitionState,
    JoinEdge,
    TeamMode,
    VariableSpec,
)
from cteforge.domain.users import User
from cteforge.domain.variables import Role
from cteforge.parsing.analyze import analyze, generate, parse
from cteforge.services import _library
from cteforge.services.auth import permitted
from cteforge.services.cards import CompositionCard, PartCard, json_safe
from cteforge.services.context import AppContext, team_mode
from cteforge.services.import_query import compare_queries
from cteforge.templating.render import render
from cteforge.templating.roundtrip import roundtrip_check
from cteforge.templating.template import apply_annotations
from cteforge.warehouse.base import Warehouse

MAX_DEPTH = 10  # Nesting deeper than this is refused (and is surely a mistake).


def _issue(code: IssueCode, severity: Severity, message: str, variable: str | None = None) -> Issue:
    return Issue(code=code, severity=severity, message=message, variable=variable)


def cycle_error(chain: list[str]) -> PolicyError:
    message = (
        "Composition cycle: " + " → ".join(chain) + ". A composed definition may not use "
        "itself, also not through other composed definitions."
    )
    error = PolicyError(f"composition_cycle: {message}")
    error.issues = [_issue(IssueCode.COMPOSITION_CYCLE, Severity.ERROR, message)]  # type: ignore[attr-defined]
    return error


def require_exploratory(user: User, raw: list[str] | tuple[str, ...]) -> None:
    """A final SELECT that reads raw tables (or table functions) is exploratory SQL: it
    needs check_and_run as well as compile_and_run (ADR 0027), or a token without
    check_and_run could run any SQL through a `select` — an agent's, or a composed
    definition's stored one (ADR 0026). Raised before anything reaches the warehouse."""
    if not permitted(user, Tool.CHECK_AND_RUN):
        raise PermissionDeniedError(
            f"The final SELECT reads raw table(s) {sorted(set(raw))} directly: that is "
            f"exploratory SQL and needs the check_and_run tool, which user '{user.name}' "
            "does not hold. Select only from the definitions' aliases."
        )


# ── Resolving parts ──────────────────────────────────────────────────────────────────


@dataclass
class Resolved:
    """A part's definition now, and what a caller should know about it."""

    definition: Definition
    issues: list[Issue] = field(default_factory=list)
    usable: bool = True
    latest_version: int | None = None


def resolve(ctx: AppContext, alias: str, ref: DefinitionRef, mode: TeamMode) -> Resolved:
    """The part `ref` resolves to now (NotFoundError when it does not exist; a former id
    names the current one)."""
    usable = _library.usable_states(mode)
    if ref.version is not None:
        definition = _library.get_definition(ctx, ref.id, ref.version)
        latest = ctx.store.latest_version(ref.id)
        issues = []
        if latest is not None and latest > ref.version:
            issues.append(
                _issue(
                    IssueCode.PART_SUPERSEDED,
                    Severity.INFO,
                    f"Part '{alias}' is pinned to '{ref.id}' v{ref.version}; the latest "
                    f"version is v{latest}. Pinned parts do not move on their own.",
                )
            )
        return Resolved(
            definition, issues, usable=definition.state in usable, latest_version=latest
        )
    latest_def = _library.get_definition(ctx, ref.id)
    found = _library.newest_in_states(ctx, latest_def, usable)
    if found is not None:
        return Resolved(found, latest_version=latest_def.version)
    message = (
        f"Part '{alias}' floats on '{ref.id}', which has no version usable in {mode.value} "
        f"mode (latest v{latest_def.version} is {latest_def.state.value}). The composed "
        "definition can't be used until that part is usable again."
    )
    return Resolved(
        latest_def,
        [_issue(IssueCode.PART_NOT_USABLE, Severity.ERROR, message)],
        usable=False,
        latest_version=latest_def.version,
    )


# ── Values ───────────────────────────────────────────────────────────────────────────


def split_values(
    definition: Definition, values: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """(own final-SELECT values, alias → part values) of a composed definition's values.
    RenderError for a value naming no alias and no own variable."""
    assert definition.composition is not None
    aliases = definition.composition.uses
    own: dict[str, Any] = {}
    parts: dict[str, dict[str, Any]] = {}
    for key, value in values.items():
        alias, dot, rest = key.partition(".")
        if dot and alias in aliases and rest:
            parts.setdefault(alias, {})[rest] = value
        elif not dot and key in aliases:
            if not isinstance(value, Mapping):
                raise RenderError(
                    f"'{key}' is a part alias: give its values as an object of variable → "
                    f"value, or as '{key}.<variable>'."
                )
            parts.setdefault(key, {}).update(value)
        elif dot:
            raise RenderError(
                f"'{key}' names no part of '{definition.id}'; its parts are {sorted(aliases)}."
            )
        else:
            own[key] = value
    return own, parts


def _part_values(
    definition: Definition, alias: str, given: dict[str, Any]
) -> tuple[dict[str, Any], list[Issue]]:
    """`fixed` values for `alias` with the caller's on top; an override of a fixed one is
    a `fixed_override` warning."""
    assert definition.composition is not None
    fixed = dict(definition.composition.fixed.get(alias, {}))
    issues = [
        _issue(
            IssueCode.FIXED_OVERRIDE,
            Severity.WARNING,
            f"'{alias}.{name}' is fixed by '{definition.id}' to "
            f"{json.dumps(json_safe(fixed[name]))}; overriding it means the result no longer "
            "matches the composed definition as written.",
            f"{alias}.{name}",
        )
        for name, value in given.items()
        if name in fixed and value != fixed[name]
    ]
    return fixed | given, issues


def _defaults_used(prefix: str, definition: Definition, given: Mapping[str, Any]) -> list[Issue]:
    """An info issue per changeable variable left out: it keeps the trusted query's
    original value, which is rarely what a new question means."""
    return [
        _issue(
            IssueCode.DEFAULT_VALUE_USED,
            Severity.INFO,
            f"'{prefix}{v.name}' was not given, so the original value "
            f"{json.dumps(json_safe(v.original_value))} was used. Pass it in `values` if the "
            "question needs another one.",
            v.name,
        )
        for v in definition.variables
        if v.role is Role.CHANGEABLE and v.name not in given
    ]


# ── Compiling ────────────────────────────────────────────────────────────────────────


@dataclass
class Compiled:
    sql: str
    dialect: Dialect
    issues: list[Issue] = field(default_factory=list)
    values: dict[str, Any] = field(default_factory=dict)  # plain values, dotted names
    sql_definitions: list[Definition] = field(default_factory=list)  # leaves, every level
    composed_definitions: list[Definition] = field(default_factory=list)  # self + nested
    joins_known: bool = True  # every unverified composition's final SELECT joins
    raw_tables: list[str] = field(default_factory=list)  # read by unverified final SELECTs
    # Raw tables read by ANY composed definition's final SELECT, verified or not (the
    # check_and_run gate, ADR 0027: it is about running exploratory SQL, not trust).
    reads_raw: list[str] = field(default_factory=list)
    blocking: list[Issue] = field(default_factory=list)  # part_not_usable

    @property
    def used(self) -> list[Definition]:
        """Every definition version used, outermost first, without repeats."""
        seen: dict[str, Definition] = {}
        for d in [*self.composed_definitions, *self.sql_definitions]:
            seen.setdefault(f"{d.id}@{d.version}", d)
        return list(seen.values())


class _Edges:
    """Known join-map edges, read once per compile and only when needed."""

    def __init__(self, ctx: AppContext) -> None:
        self._ctx = ctx
        self._edges: list[JoinEdge] | None = None

    def get(self) -> list[JoinEdge]:
        if self._edges is None:
            self._edges = _library.known_edges(self._ctx)
        return self._edges


def compile_definition(
    ctx: AppContext,
    definition: Definition,
    values: Mapping[str, Any],
    target: Dialect,
    *,
    mode: TeamMode | None = None,
    prefix: str = "",
    _stack: tuple[str, ...] = (),
    _edges: _Edges | None = None,
) -> Compiled:
    """Render `definition` with `values` into one query in `target` (see the module
    docstring). `prefix` names it in messages (e.g. "orders."). Raises RenderError /
    VariableValueError for bad values, NotFoundError for a missing part, PolicyError for a
    cycle or too deep nesting."""
    mode = mode or team_mode(ctx)
    edges = _edges or _Edges(ctx)
    if definition.composition is None:
        rendered = render(definition, dict(values), target)
        return Compiled(
            sql=rendered.sql,
            dialect=target,
            issues=[*rendered.issues, *_defaults_used(prefix, definition, values)],
            values=rendered.values,
            sql_definitions=[definition],
        )
    stack = (*_stack, definition.id)
    if definition.id in _stack:
        raise cycle_error(list(stack))
    if len(stack) > MAX_DEPTH:
        raise PolicyError(f"Compositions nest more than {MAX_DEPTH} levels deep: {stack}")
    own, given = split_values(definition, values)
    out = Compiled(sql="", dialect=target, composed_definitions=[definition])
    parts: list[Part] = []
    for alias, ref in definition.composition.uses.items():
        if ref.id in stack:
            raise cycle_error([*stack, ref.id])
        resolved = resolve(ctx, alias, ref, mode)
        out.issues += resolved.issues
        out.blocking += [i for i in resolved.issues if i.code is IssueCode.PART_NOT_USABLE]
        part_values, override_issues = _part_values(definition, alias, given.get(alias, {}))
        out.issues += override_issues
        child = compile_definition(
            ctx,
            resolved.definition,
            part_values,
            target,
            mode=mode,
            prefix=f"{prefix}{alias}.",
            _stack=stack,
            _edges=edges,
        )
        out.issues += child.issues
        out.blocking += child.blocking
        out.values |= {f"{alias}.{k}": v for k, v in child.values.items()}
        out.sql_definitions += child.sql_definitions
        out.composed_definitions += child.composed_definitions
        out.joins_known = out.joins_known and child.joins_known
        out.raw_tables += child.raw_tables
        out.reads_raw += child.reads_raw
        try:
            parts.append(Part(alias=alias, definition=resolved.definition, sql=child.sql))
        except ValidationError:
            raise RenderError(f"Alias '{alias}' must be lower_snake_case.") from None
    select: str | None = None
    if definition.template.strip():
        rendered = render(definition, own, target)
        select = rendered.sql
        out.issues += rendered.issues
        out.values |= rendered.values
        out.issues += _defaults_used(prefix, definition, own)
    elif own:
        raise RenderError(
            f"'{definition.id}' has no variables of its own: name parts' variables as "
            f"'<alias>.<variable>' ({sorted(own)} given)."
        )
    composed = compose(parts, select, target)
    if select is not None:
        out.reads_raw += list(composed.raw_tables)
    reviewed = definition.state is DefinitionState.VERIFIED
    if reviewed:
        # A person verified this final SELECT and choice of parts as a whole.
        out.issues += [i for i in composed.issues if i.code is not IssueCode.UNKNOWN_JOIN]
    else:
        out.issues += composed.issues
        if select is not None:
            known, join_issues = _library.final_select_joins(
                composed,
                set(definition.composition.uses),
                edges.get() if (composed.joins or composed.raw_tables) else [],
            )
            out.issues += join_issues
            out.joins_known = out.joins_known and known
            out.raw_tables += list(composed.raw_tables)
    out.sql = composed.sql
    return out


def compiled_sql(
    ctx: AppContext, definition: Definition, target: Dialect | None = None
) -> str | None:
    """The whole query of `definition` with its original values (None when it can't be
    compiled, e.g. a missing part). For mining joins and cards; never raises."""
    try:
        return compile_definition(ctx, definition, {}, target or definition.source_dialect).sql
    except CteforgeError:
        return None


# ── Checks for proposals ─────────────────────────────────────────────────────────────


def cycle_issue(ctx: AppContext, definition: Definition) -> Issue | None:
    """A `composition_cycle` error when `definition` (not stored yet, or a revision) uses
    itself through its parts, at any depth (latest versions and pinned ones)."""
    if definition.composition is None:
        return None

    def walk(current: Definition, path: list[str]) -> list[str] | None:
        if current.composition is None or len(path) > MAX_DEPTH:
            return None
        for ref in current.composition.uses.values():
            if ref.id == definition.id:
                return [*path, ref.id]
            if ref.id in path:
                continue  # a cycle not through `definition`: reported by that one
            part = ctx.store.get_definition(ref.id, ref.version)
            if part is not None:
                found = walk(part, [*path, ref.id])
                if found is not None:
                    return found
        return None

    chain = walk(definition, [definition.id])
    if chain is None:
        return None
    return cycle_error(chain).issues[0]  # type: ignore[attr-defined]


# ── Cards ────────────────────────────────────────────────────────────────────────────


def open_variables(
    ctx: AppContext, definition: Definition, mode: TeamMode, _depth: int = 0
) -> list[tuple[str, VariableSpec]]:
    """(qualified name, spec) of every variable a caller may set: a SQL definition's own;
    a composed definition's final-SELECT variables plus its parts' open variables that
    `fixed` does not set, as `<alias>.<var>`. Parts that can't be resolved are skipped."""
    own = [(v.name, v) for v in definition.variables]
    if definition.composition is None or _depth > MAX_DEPTH:
        return own
    out = list(own)
    for alias, ref in definition.composition.uses.items():
        try:
            part = resolve(ctx, alias, ref, mode).definition
        except CteforgeError:
            continue
        fixed = definition.composition.fixed.get(alias, {})
        for name, spec in open_variables(ctx, part, mode, _depth + 1):
            if name not in fixed:
                out.append((f"{alias}.{name}", spec))
    return out


def composition_card(
    ctx: AppContext, definition: Definition, *, mode: TeamMode | None = None
) -> CompositionCard | None:
    """Parts (with what they resolve to now), fixed and open variables, the final SELECT
    and the full compiled SQL with original values. None for a SQL definition."""
    if definition.composition is None:
        return None
    mode = mode or team_mode(ctx)
    parts: list[PartCard] = []
    issues: list[Issue] = []
    for alias, ref in definition.composition.uses.items():
        try:
            resolved = resolve(ctx, alias, ref, mode)
        except CteforgeError as exc:
            issues.append(_issue(IssueCode.NOT_FOUND, Severity.ERROR, f"Part '{alias}': {exc}"))
            parts.append(PartCard(alias=alias, id=ref.id, pinned_version=ref.version, usable=False))
            continue
        d = resolved.definition
        issues += resolved.issues
        parts.append(
            PartCard(
                alias=alias,
                id=ref.id,
                pinned_version=ref.version,
                version=d.version,
                title=d.title,
                state=d.state,
                latest_version=resolved.latest_version,
                superseded=ref.version is not None
                and resolved.latest_version is not None
                and resolved.latest_version > ref.version,
                usable=resolved.usable,
                composed=d.composition is not None,
            )
        )
    sql: str | None = None
    try:
        compiled = compile_definition(ctx, definition, {}, definition.source_dialect, mode=mode)
        sql = compiled.sql
        issues += [i for i in compiled.issues if i.code is not IssueCode.DEFAULT_VALUE_USED]
    except CteforgeError as exc:
        issues.append(
            _issue(IssueCode.COMPOSITION_INVALID, Severity.ERROR, f"Does not compile: {exc}")
        )
    return CompositionCard(
        parts=parts,
        fixed=json_safe(definition.composition.fixed),
        open_variables=[name for name, _ in open_variables(ctx, definition, mode)],
        select=definition.template,
        compiled_sql=sql,
        dialect=definition.source_dialect,
        issues=list(dict.fromkeys(issues)),
    )


# ── Proposal checks ──────────────────────────────────────────────────────────────────

# Metadata a composed definition takes from the proposal (as for SQL definitions).
_METADATA = (
    "id",
    "title",
    "meaning",
    "keywords",
    "use_when",
    "dont_use_when",
    "grain",
    "join_keys",
    "owner",
    "profile",
    "version",
)


def _not_read_only(select: str, dialect: Dialect) -> None:
    try:
        ensure_query(parse(select, dialect), "composition's final SELECT")
    except RenderError as exc:
        message = str(exc)
        error = PolicyError(f"not_read_only: {message}")
        error.issues = [_issue(IssueCode.NOT_READ_ONLY, Severity.ERROR, message)]  # type: ignore[attr-defined]
        raise error from None


def _invalid(message: str) -> Issue:
    return _issue(IssueCode.COMPOSITION_INVALID, Severity.ERROR, message)


def checked_composition(
    ctx: AppContext,
    user: User,
    composition: Composition,
    select: str | None,
    dialect: Dialect,
    annotations: list[Annotation],
    metadata: Mapping[str, Any],
    warehouse: Warehouse | None,
) -> tuple[Definition, list[Issue]]:
    """Build a composed definition from parts + final SELECT (+ annotations of its own
    literals) and run its checks (module docstring) as `user`. A final SELECT reading raw
    tables needs `user` to hold check_and_run before the checks run it on the warehouse
    (PermissionDeniedError; ADR 0027). Raises PolicyError (not read-only),
    SqlParseError / AnnotationError (no template can be built), CodecError (invalid
    metadata or composition)."""
    select = select if select and select.strip() else None
    if select is None and annotations:
        raise CodecError("Annotations need a final SELECT (they annotate its literals)")
    issues: list[Issue] = []
    template, variables, outputs = "", [], []
    analysis = None
    if select is not None:
        _not_read_only(select, dialect)
        analysis = analyze(select, dialect)
        annotated = apply_annotations(select, dialect, analysis, annotations)
        template, variables, outputs = annotated.template, annotated.variables, analysis.outputs
        unknown_types = [i for i in annotated.issues if i.code is IssueCode.TYPE_UNKNOWN]
        issues += [i for i in annotated.issues if i.code is not IssueCode.TYPE_UNKNOWN]
        if unknown_types:
            issues.append(
                _issue(
                    IssueCode.TYPE_UNKNOWN,
                    Severity.INFO,
                    "The final SELECT reads its parts, not warehouse tables, so its "
                    "variables' types were not checked against column types; the results "
                    "comparison checks the original values.",
                )
            )
    fields = {k: metadata[k] for k in _METADATA if k in metadata}
    try:
        candidate = Definition.model_validate(
            fields
            | {
                "source_dialect": dialect,
                "template": template,
                "variables": variables,
                "composition": composition,
                "outputs": outputs,
                "state": DefinitionState.DRAFT,
            }
        )
    except ValidationError as exc:
        detail = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        raise CodecError(f"Invalid composed definition: {detail}") from None
    if select is not None and not roundtrip_check(select, candidate).identical:
        issues.append(
            _issue(
                IssueCode.ROUNDTRIP_SQL_DIFFERS,
                Severity.ERROR,
                "Rendering the final SELECT with its original values does not reproduce it.",
            )
        )

    mode = team_mode(ctx)
    tables: list[str] = []
    single_outputs: list[str] = []
    for alias, ref in composition.uses.items():
        try:
            resolved = resolve(ctx, alias, ref, mode)
        except NotFoundError as exc:
            issues.append(_issue(IssueCode.NOT_FOUND, Severity.ERROR, f"Part '{alias}': {exc}"))
            continue
        part = resolved.definition
        tables += part.tables
        single_outputs = list(part.outputs)
        if part.state is DefinitionState.DRAFT:
            issues.append(
                _issue(
                    IssueCode.DRAFT_NOT_USABLE,
                    Severity.WARNING,
                    f"Part '{alias}' ('{ref.id}' v{part.version}) is a draft; the composed "
                    "definition can't run until it passes its checks.",
                )
            )
        names = {name for name, _ in open_variables(ctx, part, mode)}
        unknown = sorted(set(composition.fixed.get(alias, {})) - names)
        if unknown:
            issues.append(
                _invalid(
                    f"fixed sets {unknown} on part '{alias}' ('{ref.id}'), which has no such "
                    f"open variable (it has: {sorted(names) or 'none'})."
                )
            )
    cycle = cycle_issue(ctx, candidate)
    if cycle is not None:
        issues.append(cycle)

    results_match: bool | None = None
    ran = False
    raw_tables: list[str] = []
    if not has_errors(issues):
        target = warehouse.dialect if warehouse is not None else dialect
        try:
            compiled = compile_definition(ctx, candidate, {}, target, mode=mode)
        except CteforgeError as exc:
            issues.append(_invalid(f"The composition does not compile: {exc}"))
        else:
            issues += [
                _as_warning(i) if i.code is IssueCode.PART_NOT_USABLE else i
                for i in compiled.issues
                if i.code not in (IssueCode.DEFAULT_VALUE_USED, IssueCode.PART_SUPERSEDED)
                and i not in issues
            ]
            raw_tables = compiled.raw_tables
            if compiled.reads_raw and warehouse is not None:
                # The checks run the query: reading raw tables is exploratory SQL.
                require_exploratory(user, compiled.reads_raw)
            if warehouse is None:
                issues.append(
                    _issue(
                        IssueCode.CHECKS_INCOMPLETE,
                        Severity.WARNING,
                        "No warehouse: the composed query was compiled but not checked "
                        "with EXPLAIN.",
                    )
                )
            elif variables:
                plain = candidate.model_copy(
                    update={"template": parse_normalized(select or "", dialect), "variables": []}
                )
                try:
                    original = compile_definition(ctx, plain, {}, target, mode=mode).sql
                except CteforgeError as exc:
                    issues.append(_invalid(f"The composition does not compile: {exc}"))
                else:
                    results_match, run_issues = compare_queries(original, compiled.sql, warehouse)
                    issues += run_issues
                    ran = True
            else:
                try:
                    warehouse.explain(compiled.sql)
                    ran = True
                except WarehouseError as exc:
                    issues.append(_issue(IssueCode.COMPILE_FAILED, Severity.ERROR, str(exc)))
    incomplete = any(i.code is IssueCode.CHECKS_INCOMPLETE for i in issues)
    ok = ran and not has_errors(issues) and not incomplete and results_match is not False
    if variables and results_match is not True:
        ok = False
    state = DefinitionState.LLM_ANNOTATED if ok else DefinitionState.DRAFT
    all_tables = list(dict.fromkeys([*tables, *raw_tables]))
    definition = candidate.model_copy(
        update={
            "tables": all_tables,
            "outputs": outputs if select is not None else single_outputs,
            "state": state,
        }
    )
    return definition, issues


def _as_warning(issue: Issue) -> Issue:
    """A part not usable in the team mode right now is a fact about the parts, not a
    failure of the composition: a warning (it can be approved, not verified)."""
    return issue.model_copy(update={"severity": Severity.WARNING})


def parse_normalized(sql: str, dialect: Dialect) -> str:
    """`sql` as the template of a definition without variables (normalized formatting)."""
    return generate(parse(sql, dialect), dialect)
