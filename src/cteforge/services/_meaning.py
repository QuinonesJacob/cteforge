"""Meaning search plumbing shared by search, describe, search_index and _apply (support
module; DESIGN §19, ADR 0023). No permission checks and no audit events of its own.

- Search document of a stored definition: `domain.search.search_document` with the joins
  mined from its SQL (`_library.mined_edges`).
- Vectors: one per definition version per model in the store (`search_vectors`), made from
  the search document with `ctx.embedder` (documents as passages, the query as a query).
  Derived data: a missing or out-of-date vector only means that definition is matched by
  keyword alone. Nothing here ever fails a write.
- Indexing: `index_definition` after a version is applied (only when the model is already
  loaded: an approval never waits for, or starts, a model download); `ensure_vectors`
  fills in missing ones (every search does, for the candidates it ranks; a long-running
  server at most `INCREMENTAL_LIMIT` per search, its background warm-up does the rest);
  `warm_up` loads the model and backfills everything search could show, re-embedding
  vectors whose document changed (`check_documents`).
- Similarity: cosine (vectors are unit length, so a dot product), in Python — libraries
  are thousands of definitions, i.e. milliseconds.

Meaning thresholds (chosen with the real model on the demo library plus held-out
unrelated queries; see ADR 0023 "Calibration"):
- `MIN_SIMILARITY` 0.58: bge-small similarities of a short query to any business-flavoured
  document sit around 0.35–0.60 even when unrelated ("weather forecast tomorrow" 0.44,
  "football scores" 0.54); paraphrases of a definition's meaning score 0.58–0.81 ("how
  much of the money we billed did we actually receive" → realization_rate 0.593,
  booked_vs_collected 0.582). Below it a definition does not count as a meaning match.
- `MAX_GAP` 0.08: in a large library only definitions within 0.08 of the best match
  count, so a query does not pull in every loosely related definition.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from cteforge.domain.errors import CteforgeError, EmbeddingError
from cteforge.domain.models import Definition, DefinitionState
from cteforge.domain.search import SearchVector, document_sha256, search_document
from cteforge.embedding.base import Embedder, ModelStatus
from cteforge.services import _library
from cteforge.services.context import AppContext

_log = logging.getLogger(__name__)

MIN_SIMILARITY = 0.58
MAX_GAP = 0.08
INCREMENTAL_LIMIT = 16
EMBED_BATCH = 32

NOTE_LOADING = (
    "Meaning search is starting (the embedding model is loading); "
    "these results match by keyword only."
)
NOTE_FAILED = (
    "Meaning search is unavailable (the embedding model could not be loaded); "
    "these results match by keyword only."
)
NOTE_ERROR = "Meaning search failed for this query; these results match by keyword only."

Key = tuple[str, int]


def document_for(definition: Definition, former_ids: Sequence[str] = ()) -> str:
    """The search document of a stored definition version (with its mined joins and its
    former ids, ADR 0024: `former_ids_of`)."""
    return search_document(definition, _library.mined_edges(definition), former_ids)


def former_ids_of(ctx: AppContext) -> dict[str, list[str]]:
    """Current definition id → its former ids (ADR 0024)."""
    out: dict[str, list[str]] = {}
    for former in ctx.store.former_ids():
        out.setdefault(former.definition_id, []).append(former.former_id)
    return out


@dataclass(frozen=True)
class Meaning:
    """Meaning-search outcome for one query: `similarities` of the definitions that count
    as a meaning match (≥ MIN_SIMILARITY, within MAX_GAP of the best), by (id, version);
    `note` when meaning search was wanted but could not run."""

    similarities: dict[Key, float]
    note: str | None = None


@dataclass(frozen=True)
class IndexCounts:
    embedded: int = 0
    unchanged: int = 0
    failed: int = 0


def _key(d: Definition) -> Key:
    return (d.id, d.version)


def _stored(ctx: AppContext, embedder: Embedder) -> dict[Key, SearchVector]:
    return {
        (v.definition_id, v.version): v
        for v in ctx.store.search_vectors(embedder.model_name)
        if v.dim == embedder.dim
    }


def ensure_vectors(
    ctx: AppContext,
    definitions: Iterable[Definition],
    *,
    check_documents: bool = False,
    limit: int | None = None,
) -> IndexCounts:
    """Embed and store the vectors `definitions` lack (with `check_documents`, also those
    whose stored document hash differs from today's document), at most `limit` of them.
    Loads the model if needed. Raises EmbeddingError / StoreError."""
    embedder = ctx.embedder
    if embedder is None:
        return IndexCounts()
    stored = _stored(ctx, embedder)
    formers = former_ids_of(ctx)
    todo: list[tuple[Definition, str]] = []
    unchanged = 0
    seen: set[Key] = set()
    for d in definitions:
        if _key(d) in seen:
            continue
        seen.add(_key(d))
        current = stored.get(_key(d))
        if current is not None and not check_documents:
            unchanged += 1
            continue
        doc = document_for(d, formers.get(d.id, ()))
        if current is not None and current.doc_sha256 == document_sha256(doc):
            unchanged += 1
            continue
        todo.append((d, doc))
    if limit is not None:
        todo = todo[:limit]
    embedded = 0
    for start in range(0, len(todo), EMBED_BATCH):
        batch = todo[start : start + EMBED_BATCH]
        vectors = embedder.embed([doc for _, doc in batch])
        for (d, doc), vector in zip(batch, vectors, strict=True):
            ctx.store.put_search_vector(
                SearchVector(
                    definition_id=d.id,
                    version=d.version,
                    model=embedder.model_name,
                    doc_sha256=document_sha256(doc),
                    vector=tuple(vector),
                )
            )
            embedded += 1
    return IndexCounts(embedded=embedded, unchanged=unchanged)


def index_definition(ctx: AppContext, definition: Definition) -> bool:
    """Store `definition`'s vector when the model is already loaded. Never raises and never
    loads the model: on any failure the definition is matched by keyword until a later
    search or warm-up embeds it. True when a vector was stored."""
    embedder = ctx.embedder
    if embedder is None or embedder.status() is not ModelStatus.READY:
        return False
    try:
        return ensure_vectors(ctx, [definition], check_documents=True).embedded > 0
    except Exception:  # noqa: BLE001 — derived data must never fail the write
        _log.warning(
            "search vector for %s@%s not stored; it is matched by keyword until re-indexed",
            definition.id,
            definition.version,
            exc_info=True,
        )
        return False


def indexable(ctx: AppContext) -> list[Definition]:
    """Every version search can show: per definition its latest version and its newest
    verified / usable-in-open-mode versions (strict and open mode, drafts or not)."""
    found: dict[Key, Definition] = {}
    usable_open = {DefinitionState.VERIFIED, DefinitionState.LLM_ANNOTATED}
    for latest in ctx.store.list_definitions():
        found[_key(latest)] = latest
        for states in ({DefinitionState.VERIFIED}, usable_open):
            d = _library.newest_in_states(ctx, latest, states)
            if d is not None:
                found.setdefault(_key(d), d)
    return list(found.values())


def warm_up(ctx: AppContext) -> IndexCounts:
    """Load the model and embed every version search can show whose vector is missing or
    out of date. Raises EmbeddingError / StoreError."""
    if ctx.embedder is None:
        return IndexCounts()
    ctx.embedder.load()
    return ensure_vectors(ctx, indexable(ctx), check_documents=True)


def start_background(ctx: AppContext) -> threading.Thread | None:
    """For long-running servers: from now on requests never wait for the model, and a
    daemon thread loads it and backfills the vectors. None when meaning search is off."""
    ctx.background_model = True
    if ctx.embedder is None:
        return None

    def run() -> None:
        try:
            counts = warm_up(ctx)
            _log.info("meaning search ready: %s vectors embedded", counts.embedded)
        except CteforgeError as exc:
            _log.warning("meaning search unavailable: %s", exc)
        except Exception:  # noqa: BLE001 — e.g. the context closed during shutdown
            _log.warning("meaning search warm-up stopped", exc_info=True)

    thread = threading.Thread(target=run, name="cteforge-search-warm-up", daemon=True)
    thread.start()
    return thread


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def similarities(ctx: AppContext, query: str, candidates: Sequence[Definition]) -> Meaning:
    """Meaning matches of `query` among `candidates` (see `Meaning`). Off (no embedder) →
    no matches and no note. A model that is not ready: a server (`ctx.background_model`)
    starts loading it in the background and answers by keyword now; otherwise (CLI) it
    loads it and waits. Never raises."""
    embedder = ctx.embedder
    if embedder is None or not query.strip() or not candidates:
        return Meaning({})
    if embedder.status() is not ModelStatus.READY:
        if ctx.background_model:
            embedder.load_in_background()
            loading = embedder.status() is ModelStatus.LOADING
            return Meaning({}, NOTE_LOADING if loading else NOTE_FAILED)
        try:
            embedder.load()
        except EmbeddingError as exc:
            _log.warning("meaning search unavailable: %s", exc)
            return Meaning({}, NOTE_FAILED)
    try:
        limit = INCREMENTAL_LIMIT if ctx.background_model else None
        ensure_vectors(ctx, candidates, limit=limit)
        query_vector = embedder.embed([query], query=True)[0]
        stored = _stored(ctx, embedder)
    except Exception:  # noqa: BLE001 — search must still answer by keyword
        _log.warning("meaning search failed for one query", exc_info=True)
        return Meaning({}, NOTE_ERROR)
    scores = {
        _key(d): _dot(query_vector, stored[_key(d)].vector) for d in candidates if _key(d) in stored
    }
    if not scores:
        return Meaning({})
    best = max(scores.values())
    floor = max(MIN_SIMILARITY, best - MAX_GAP)
    return Meaning({k: s for k, s in scores.items() if s >= floor})
