"""Library lookups shared by the read/run and the write/review use-cases (support module).

No permission checks and no audit events of its own: callers `require` and record.

- Version visibility (DESIGN §8, §15, §19): which definition states a team mode lets agents
  use, and the newest version of a definition in such a state. `search` shows, and
  `compile_and_run` floats to, the same version.
- Rebuilding a stored definition for a re-check (DESIGN §7, §18): the trusted query is the
  template rendered with its original values in its source dialect; the annotations are
  the variables with the literal / grain IDs their slots replaced. IDs are structural, so
  they address the rebuilt query. Used by revise, library import and revalidate.
- The join map (DESIGN §3, §17): canonical edges and their deterministic ids, mining edges
  from a definition's SQL (joins between two BASE tables only; CTE sides are skipped),
  recomputing `usage_count`, and matching a join against the edges.
- Input parsing shared by several use-cases (`parse_dialect`, `resolve_dialect`: an
  omitted dialect → the connection's: the profile passed, else the default connection).
- Definition lookup by id (ADR 0024): `get_definition` / `definition_not_found` — the ONE
  place a missing id becomes a NotFoundError, so a former id (renamed) fails naming the
  current id in every caller (describe, history, run, recipes, verify, revise,
  revalidate); `check_id_available` refuses a new id that exists or is another
  definition's former id; `definition_references` lists what inside cteforge uses an id.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from cteforge.domain.codec import AnnotationFile
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import CteforgeError, NotFoundError, PolicyError
from cteforge.domain.models import (
    Annotation,
    Definition,
    DefinitionState,
    FormerId,
    JoinEdge,
    JoinKey,
    TeamMode,
)
from cteforge.domain.proposals import DEFINITION_KINDS, ProposalStatus
from cteforge.parsing.analyze import analyze
from cteforge.services.cards import (
    DefinitionReferences,
    ProposalReference,
    RecipeReference,
)
from cteforge.services.context import AppContext
from cteforge.templating.render import render

# ── Inputs ───────────────────────────────────────────────────────────────────────────


def parse_dialect(value: Dialect | str) -> Dialect:
    """A Dialect from user input; PolicyError (not ValueError) for an unknown name."""
    try:
        return Dialect(value)
    except ValueError:
        options = ", ".join(d.value for d in Dialect)
        raise PolicyError(f"Unknown dialect '{value}'; expected one of {options}") from None


def resolve_dialect(
    ctx: AppContext, dialect: Dialect | str | None, profile: str | None = None
) -> Dialect:
    """The dialect of SQL a caller sends (DESIGN §12, ADR 0021): the explicit `dialect`,
    else the connection's — the dialect of `profile`, else of the team's effective default
    connection. PolicyError when none applies; NotFoundError for an unknown profile name.
    A dialect that differs from the connection's is allowed (`dialect_mismatch`)."""
    if dialect is not None:
        return parse_dialect(dialect)
    try:
        return ctx.warehouses.summary(profile).dialect
    except PolicyError:
        raise PolicyError(
            "No dialect given: pass `dialect`, or ask an admin to set a default connection"
        ) from None


# ── Definition lookup by id (ADR 0024) ───────────────────────────────────────────────


def renamed_message(former: FormerId) -> str:
    """What a lookup of a former id says: the current id, and when it changed."""
    when = former.renamed_at.date().isoformat()
    return (
        f"Definition '{former.former_id}' was renamed to '{former.definition_id}' on {when}; "
        "use the new ID."
    )


def definition_not_found(
    ctx: AppContext, id: str, version: int | None = None, *, hint: str = ""
) -> NotFoundError:
    """The NotFoundError for a definition id (version) the store does not have. A former id
    (renamed, ADR 0024) gets a message naming the current id, so an agent with stale
    instructions corrects itself in one step; there is no silent alias. `hint` is appended
    to the plain not-found message only."""
    former = ctx.store.former_id(id)
    if former is not None:
        return NotFoundError(renamed_message(former))
    suffix = f" version {version}" if version is not None else ""
    return NotFoundError(f"Definition '{id}'{suffix} does not exist{hint}")


def get_definition(ctx: AppContext, id: str, version: int | None = None) -> Definition:
    """The stored definition (latest unless `version`); NotFoundError otherwise
    (`definition_not_found`: a former id names the current one)."""
    definition = ctx.store.get_definition(id, version)
    if definition is None:
        raise definition_not_found(ctx, id, version)
    return definition


def check_id_available(ctx: AppContext, id: str, *, renaming: str | None = None) -> None:
    """PolicyError unless `id` may become a definition's id: it must not exist and must
    not be a former id of another definition (ADR 0024: an error naming the wrong
    definition would mislead). `renaming` is the definition being renamed: taking back
    one of its own former ids is allowed (undoing a rename)."""
    if ctx.store.latest_version(id) is not None:
        raise PolicyError(f"Definition '{id}' already exists; choose another ID")
    former = ctx.store.former_id(id)
    if former is not None and former.definition_id != renaming:
        when = former.renamed_at.date().isoformat()
        raise PolicyError(
            f"'{id}' is a former ID of definition '{former.definition_id}' (renamed on "
            f"{when}); a former ID can't be reused, because lookups of it point agents at "
            f"'{former.definition_id}'. Choose another ID."
        )


OUTSIDE_WARNING = (
    "References outside cteforge (agent instructions, prompts, scripts, notebooks) are not "
    "listed and will stop working. A lookup of the old ID fails with an error naming the "
    "new ID; there is no alias."
)


def definition_references(ctx: AppContext, definition: Definition) -> DefinitionReferences:
    """Everything stored in cteforge that names `definition`'s id (ADR 0024): recipes (their
    latest version; which alias, pinned version or floating) and pending proposals that
    target it or whose recipe uses it. Join-map edges name tables, not definitions, and
    audit events are history (never rewritten), so neither is listed."""
    id = definition.id
    recipes = [
        RecipeReference(
            recipe_id=recipe.id,
            recipe_version=recipe.version,
            title=recipe.title,
            state=recipe.state,
            alias=alias,
            pinned_version=ref.version,
        )
        for recipe in ctx.store.recipes_using(id)
        for alias, ref in sorted(recipe.uses.items())
        if ref.id == id
    ]
    proposals: list[ProposalReference] = []
    for p in ctx.store.list_proposals(ProposalStatus.PENDING):
        if p.kind in DEFINITION_KINDS and p.target_id == id:
            relation = "target"
        else:
            recipe = p.payload.get("recipe")
            uses = recipe.get("uses") if isinstance(recipe, dict) else None
            if not isinstance(uses, dict) or not any(
                isinstance(ref, dict) and ref.get("id") == id for ref in uses.values()
            ):
                continue
            relation = "uses"
        proposals.append(
            ProposalReference(
                proposal_id=p.id,
                kind=p.kind,
                target_id=p.target_id,
                summary=p.summary,
                author=p.author,
                created_at=p.created_at,
                relation=relation,
            )
        )
    return DefinitionReferences(
        id=id,
        version=definition.version,
        state=definition.state,
        recipes=recipes,
        proposals=proposals,
        former_ids=ctx.store.former_ids(id),
        outside_warning=OUTSIDE_WARNING,
    )


# ── Version visibility ───────────────────────────────────────────────────────────────


def usable_states(mode: TeamMode) -> frozenset[DefinitionState]:
    """States agents may use (DESIGN §8): verified; in open mode also llm_annotated."""
    if mode is TeamMode.STRICT:
        return frozenset({DefinitionState.VERIFIED})
    return frozenset({DefinitionState.VERIFIED, DefinitionState.LLM_ANNOTATED})


def visible_states(mode: TeamMode, include_drafts: bool) -> frozenset[DefinitionState]:
    """States shown by search: the usable ones, or every state with `include_drafts`."""
    return frozenset(DefinitionState) if include_drafts else usable_states(mode)


def newest_in_states(
    ctx: AppContext, latest: Definition, states: Iterable[DefinitionState]
) -> Definition | None:
    """`latest` when its state is in `states`, else the newest earlier version that is;
    None when no version is."""
    wanted = set(states)
    if latest.state in wanted:
        return latest
    for info in reversed(ctx.store.definition_history(latest.id)):
        if info.version < latest.version and info.state in wanted:
            found = ctx.store.get_definition(latest.id, info.version)
            if found is not None:
                return found
    return None


# ── Rebuilding a stored definition ───────────────────────────────────────────────────


def original_sql(definition: Definition) -> str:
    """The trusted query reconstructed: the template rendered with its original values in
    its source dialect (formatting normalized; same AST as the original)."""
    return render(definition, {}).sql


def annotations_of(definition: Definition) -> list[Annotation]:
    """A definition's variables as annotations, using the literal IDs its slots replaced.
    They address `original_sql(definition)`."""
    return [
        Annotation(
            name=v.name,
            type=v.type,
            role=v.role,
            literals=[lit for slot in v.slots for lit in slot.literals],
            column=v.column,
            description=v.description,
            allowed=v.allowed,
            format=v.format,
            unit=v.unit,
        )
        for v in definition.variables
    ]


def annotation_file(definition: Definition) -> AnnotationFile:
    """The annotation file that produced `definition`: its header metadata plus
    `annotations_of(definition)`."""
    return AnnotationFile(
        id=definition.id,
        title=definition.title,
        meaning=definition.meaning,
        keywords=list(definition.keywords),
        use_when=definition.use_when,
        dont_use_when=definition.dont_use_when,
        grain=definition.grain,
        variables=annotations_of(definition),
    )


# ── Join map ─────────────────────────────────────────────────────────────────────────

JOIN_TYPES = ("inner", "left", "right", "full")
RELATIONSHIPS = ("one_to_one", "one_to_many", "many_to_one", "many_to_many")
_FLIPPED = {
    "left": "right",
    "right": "left",
    "one_to_many": "many_to_one",
    "many_to_one": "one_to_many",
}


def join_edge_id(left_table: str, right_table: str, keys: Sequence[JoinKey]) -> str:
    """Deterministic id: `<left>__<right>__<lkey>=<rkey>[,...]`, lowercase (JoinEdge)."""
    pairs = ",".join(f"{k.left}={k.right}" for k in keys)
    return f"{left_table}__{right_table}__{pairs}".lower()


def canonical_edge(
    left_table: str,
    right_table: str,
    keys: Sequence[JoinKey | tuple[str, str]],
    join_type: str = "inner",
    **fields: Any,
) -> JoinEdge:
    """A JoinEdge oriented so the alphabetically first table is on the left (keys, left/right
    join types and one_to_many/many_to_one flipped with it), keys lowercased, sorted and
    de-duplicated."""
    lt, rt = left_table.strip().lower(), right_table.strip().lower()
    pairs = [(k.left, k.right) if isinstance(k, JoinKey) else (str(k[0]), str(k[1])) for k in keys]
    pairs = [(a.strip().lower(), b.strip().lower()) for a, b in pairs]
    jt = join_type.strip().lower()
    if rt < lt:
        lt, rt = rt, lt
        pairs = [(b, a) for a, b in pairs]
        jt = _FLIPPED.get(jt, jt)
        if fields.get("relationship") is not None:
            fields["relationship"] = _FLIPPED.get(fields["relationship"], fields["relationship"])
    key_models = [JoinKey(left=a, right=b) for a, b in sorted(set(pairs))]
    return JoinEdge(
        id=join_edge_id(lt, rt, key_models),
        left_table=lt,
        right_table=rt,
        keys=key_models,
        join_type=jt,
        **fields,
    )


def mined_edges(definition: Definition) -> list[JoinEdge]:
    """Joins between two base tables in the definition's SQL, also through CTEs / derived
    tables whose key columns pass through unchanged from one base table (JoinInfo's
    `*_base_table` / `base_keys`); a join through CTEs that lands on one table on both sides
    is skipped. Empty when it can't be rendered or analyzed (mining is best-effort)."""
    try:
        analysis = analyze(original_sql(definition), definition.source_dialect)
    except CteforgeError:
        return []
    tables = {t.lower() for t in analysis.tables}
    edges: dict[str, JoinEdge] = {}
    for join in analysis.joins:
        left, right, keys = join.left_base_table, join.right_base_table, join.base_keys
        if join.join_type not in JOIN_TYPES or not keys or left is None or right is None:
            continue
        if left.lower() not in tables or right.lower() not in tables:
            continue
        through_cte = (join.left_table, join.right_table) != (left, right)
        if through_cte and left.lower() == right.lower():
            # Two CTEs over one table (e.g. two aggregates on region): grain alignment,
            # not a relationship between tables.
            continue
        edge = canonical_edge(left, right, keys, join.join_type)
        edges.setdefault(edge.id, edge)
    return list(edges.values())


def refresh_join_map(ctx: AppContext, mined_from: Definition | None = None) -> None:
    """Upsert edges mined from `mined_from` (new ones as llm_annotated) and recompute
    `usage_count` of every edge: distinct latest definitions whose SQL uses it."""
    usage: dict[str, int] = {}
    for definition in ctx.store.list_definitions():
        for edge in mined_edges(definition):
            usage[edge.id] = usage.get(edge.id, 0) + 1
    if mined_from is not None:
        for edge in mined_edges(mined_from):
            if ctx.store.get_join(edge.id) is None:
                ctx.store.upsert_join(
                    edge.model_copy(update={"usage_count": usage.get(edge.id, 0)})
                )
    for edge in ctx.store.list_joins():
        count = usage.get(edge.id, 0)
        if edge.usage_count != count:
            ctx.store.upsert_join(edge.model_copy(update={"usage_count": count}))


def same_table(a: str, b: str) -> bool:
    """Case-insensitive table equality; an unqualified name matches a qualified one by its
    last part (two qualified names must match exactly)."""
    a, b = a.lower(), b.lower()
    if a == b:
        return True
    if "." in a and "." in b:
        return False
    return a.rsplit(".", 1)[-1] == b.rsplit(".", 1)[-1]


def matching_edge(
    left: str, right: str, keys: Iterable[tuple[str, str]], edges: Iterable[JoinEdge]
) -> JoinEdge | None:
    """The join-map edge for a join of `left` and `right` on `keys`, if any: same two
    tables and exactly the same key pairs, in either orientation (case-insensitive)."""
    pairs = {(a.lower(), b.lower()) for a, b in keys}
    if not pairs:
        return None
    flipped = {(b, a) for a, b in pairs}
    for edge in edges:
        edge_pairs = {(k.left.lower(), k.right.lower()) for k in edge.keys}
        forward = same_table(left, edge.left_table) and same_table(right, edge.right_table)
        backward = same_table(left, edge.right_table) and same_table(right, edge.left_table)
        if (forward and pairs == edge_pairs) or (backward and flipped == edge_pairs):
            return edge
    return None
