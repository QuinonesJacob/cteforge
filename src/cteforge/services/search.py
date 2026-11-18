"""Use-case: search the library (DESIGN §19, ADR 0017, ADR 0023).

Hybrid: keyword (BM25, below, unchanged) and meaning (embedded search documents,
services/_meaning.py) rankings merged by reciprocal rank fusion (RRF):
- fused score = Σ over the two rankings of 1 / (RRF_K + rank), rank 1-based (equal scores
  share a rank); RRF_K = 60
  (the usual constant from Cormack et al. 2009: it damps the difference between neighbouring
  ranks, so agreement between the two rankings matters more than either rank alone).
- The keyword ranking is the BM25 hits (score > 0) by score; the meaning ranking only the
  definitions that count as a meaning match (`_meaning.MIN_SIMILARITY`, `MAX_GAP`), by
  similarity. Each hit says how it matched: `match` = keyword | meaning | both.
- Without meaning search (CTEFORGE_SEARCH_EMBEDDINGS=off, or no model) the ranking and the
  scores are exactly the keyword ones; when the model was wanted but could not run, every
  hit carries a `note` saying results are keyword-only.

Keyword scoring: lexical BM25F-style, pure Python over the store's definitions:
- Fields and weights: title 3, keywords 3, meaning 2, use_when 1, variable names and
  descriptions 1, outputs 1, tables 1, former ids 2 (ADR 0024: an old name finds the
  renamed definition), dont_use_when 0.5 (it says when NOT to use it, so a match there is
  weaker evidence).
- Tokenizer: lowercase, split on anything that is not a letter or digit (so snake_case
  splits), drop a few English stopwords, light plural stripping (`rates` → `rate`,
  `categories` → `category`, `classes` → `class`; words ending in `ss`/`us`/`is` kept).
- Ranking: hits whose only keyword matches are in `dont_use_when` come last (the words
  name what the definition is NOT for — also when it matches by meaning, since its
  document holds that text too); then score (fused when meaning ran) desc; at equal score
  (6 decimals) verified before llm_annotated before draft; then title, then id. A
  non-empty query only returns definitions that match by keyword or meaning.
- Each hit carries `use_when` / `dont_use_when` / `grain` and `matched_terms` /
  `matched_fields`, so an agent can judge a hit without fetching its card. Scores are BM25
  with no fixed scale: comparable within one search only.
- Empty query (no tokens) → every visible definition, ordered by title, score 0,
  `match` None (meaning search does not run).

Which version is searched (`_library.visible_states` / `newest_in_states`, shared with
`compile_and_run`): the newest version whose state is visible — drafts are hidden
unless `include_drafts`; in strict mode only verified versions are visible unless
`include_drafts` (then everything is). A definition whose latest version is a draft
shows its newest visible earlier version, which is what `compile_and_run` would float to.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from cteforge.domain.access import Tool
from cteforge.domain.audit import AuditKind
from cteforge.domain.models import Definition, DefinitionState
from cteforge.domain.users import User
from cteforge.services import _audit, _compose, _library, _meaning
from cteforge.services.auth import require
from cteforge.services.cards import SearchHit, search_hit, variable_card
from cteforge.services.context import AppContext, team_mode

MAX_LIMIT = 100
RRF_K = 60
K1 = 1.2
B = 0.75

FIELD_WEIGHTS: dict[str, float] = {
    "title": 3.0,
    "keywords": 3.0,
    "meaning": 2.0,
    "use_when": 1.0,
    "dont_use_when": 0.5,
    "variables": 1.0,
    "outputs": 1.0,
    "tables": 1.0,
    "former_ids": 2.0,  # IDs it had before a rename (ADR 0024): an old name still finds it.
}

STATE_RANK = {
    DefinitionState.VERIFIED: 0,
    DefinitionState.LLM_ANNOTATED: 1,
    DefinitionState.DRAFT: 2,
}

_SPLIT = re.compile(r"[^a-z0-9]+")
_STOPWORDS = frozenset(
    {"a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how", "in", "is", "it"}
    | {"of", "on", "or", "per", "the", "to", "was", "what", "when", "which", "with", "by"}
)


def _stem(word: str) -> str:
    if len(word) <= 3:
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("sses"):
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumerics, drop stopwords, strip simple plurals."""
    return [_stem(w) for w in _SPLIT.split(text.lower()) if w and w not in _STOPWORDS]


def _fields(d: Definition, former_ids: Sequence[str] = ()) -> dict[str, list[str]]:
    variables = " ".join(f"{v.name} {v.description}" for v in d.variables)
    return {
        "title": tokenize(d.title),
        "keywords": tokenize(" ".join(d.keywords)),
        "meaning": tokenize(d.meaning),
        "use_when": tokenize(d.use_when),
        "dont_use_when": tokenize(d.dont_use_when),
        "variables": tokenize(variables),
        "outputs": tokenize(" ".join(d.outputs)),
        "tables": tokenize(" ".join(d.tables)),
        "former_ids": tokenize(" ".join(former_ids)),
    }


@dataclass(frozen=True)
class _Doc:
    definition: Definition
    tf: dict[str, float]  # field-weighted term frequency
    length: float  # field-weighted length
    fields: dict[str, frozenset[str]]  # field name → its tokens

    def matches(self, terms: list[str]) -> tuple[list[str], list[str]]:
        """(query terms found, fields they were found in), both in a stable order."""
        found = [t for t in dict.fromkeys(terms) if t in self.tf]
        fields = [name for name, tokens in self.fields.items() if any(t in tokens for t in found)]
        return found, fields

    def only_dont_use_when(self, terms: list[str]) -> bool:
        """True when every matching term was found only in `dont_use_when`."""
        _, fields = self.matches(terms)
        return fields == ["dont_use_when"]


def _doc(d: Definition, former_ids: Sequence[str] = ()) -> _Doc:
    tf: Counter[str] = Counter()
    length = 0.0
    fields = _fields(d, former_ids)
    for name, tokens in fields.items():
        weight = FIELD_WEIGHTS[name]
        length += weight * len(tokens)
        for token in tokens:
            tf[token] += weight
    return _Doc(d, dict(tf), length, {k: frozenset(v) for k, v in fields.items()})


def bm25(query_terms: list[str], docs: list[_Doc]) -> list[float]:
    """BM25F-style score of each doc for the (de-duplicated) query terms."""
    if not docs:
        return []
    terms = list(dict.fromkeys(query_terms))
    n = len(docs)
    avg = sum(doc.length for doc in docs) / n or 1.0
    idf = {}
    for term in terms:
        df = sum(1 for doc in docs if term in doc.tf)
        idf[term] = math.log(1 + (n - df + 0.5) / (df + 0.5))
    scores = []
    for doc in docs:
        norm = K1 * (1 - B + B * doc.length / avg)
        score = 0.0
        for term in terms:
            f = doc.tf.get(term, 0.0)
            if f:
                score += idf[term] * f * (K1 + 1) / (f + norm)
        scores.append(score)
    return scores


def search(
    ctx: AppContext,
    user: User,
    query: str,
    *,
    limit: int = 10,
    include_drafts: bool = False,
    question: str | None = None,
) -> list[SearchHit]:
    """Ranked definitions for `query` (see module docstring). `limit` is clamped to
    1..100. Needs Tool.SEARCH."""
    with _audit.audit_failures(ctx, user, AuditKind.SEARCH, question=question):
        require(user, Tool.SEARCH)
    limit = max(1, min(limit, MAX_LIMIT))
    states = _library.visible_states(team_mode(ctx), include_drafts)
    candidates = [
        found
        for latest in ctx.store.list_definitions()
        if (found := _library.newest_in_states(ctx, latest, states)) is not None
    ]
    terms = tokenize(query or "")
    formers = _meaning.former_ids_of(ctx)
    docs = [_doc(d, formers.get(d.id, ())) for d in candidates]
    if not terms:
        ranked = sorted(docs, key=lambda doc: _order(doc, terms, 0.0))
        hits = _open_variables(
            ctx, [search_hit(doc.definition, 0.0) for doc in ranked[:limit]], ranked
        )
        _record(ctx, user, query, hits, include_drafts, question)
        return hits
    keyword = {
        _key(doc): score for score, doc in zip(bm25(terms, docs), docs, strict=True) if score > 0
    }
    meaning = _meaning.similarities(ctx, query, candidates)
    if meaning.similarities:
        scores = _fuse(keyword, meaning.similarities)
    else:
        scores = keyword
    matched = [doc for doc in docs if _key(doc) in scores]
    matched.sort(key=lambda doc: _order(doc, terms, scores[_key(doc)]))
    hits = []
    for doc in matched[:limit]:
        found, fields = doc.matches(terms)
        key = _key(doc)
        by_word, by_meaning = key in keyword, key in meaning.similarities
        kind = "both" if by_word and by_meaning else "keyword" if by_word else "meaning"
        hits.append(
            search_hit(
                doc.definition,
                round(scores[key], 6),
                matched_terms=found,
                matched_fields=fields,
                match=kind,
                note=meaning.note,
            )
        )
    hits = _open_variables(ctx, hits, matched)
    _record(ctx, user, query, hits, include_drafts, question)
    return hits


def _open_variables(ctx: AppContext, hits: list[SearchHit], docs: list[_Doc]) -> list[SearchHit]:
    """A composed definition's hit lists its open variables (`<alias>.<var>`; ADR 0026)."""
    by_key = {_key(doc): doc.definition for doc in docs}
    mode = team_mode(ctx)
    out = []
    for hit in hits:
        definition = by_key.get((hit.id, hit.version))
        if definition is not None and definition.composition is not None:
            variables = [
                variable_card(spec).model_copy(update={"name": name})
                for name, spec in _compose.open_variables(ctx, definition, mode)
            ]
            hit = hit.model_copy(update={"variables": variables})
        out.append(hit)
    return out


def _key(doc: _Doc) -> tuple[str, int]:
    return (doc.definition.id, doc.definition.version)


def _order(doc: _Doc, terms: list[str], score: float) -> tuple:
    d = doc.definition
    return (
        # Matching only where the definition says NOT to use it is no evidence for it.
        doc.only_dont_use_when(terms),
        -round(score, 6),
        STATE_RANK[d.state],
        d.title.lower(),
        d.id,
    )


def _ranks(scores: dict[tuple[str, int], float]) -> dict[tuple[str, int], int]:
    """1-based rank by score desc; equal scores (6 decimals) share the better rank."""
    ordered = sorted(scores.values(), key=lambda score: -round(score, 6))
    first: dict[float, int] = {}
    for rank, score in enumerate(ordered, start=1):
        first.setdefault(round(score, 6), rank)
    return {key: first[round(score, 6)] for key, score in scores.items()}


def _fuse(
    keyword: dict[tuple[str, int], float], meaning: dict[tuple[str, int], float]
) -> dict[tuple[str, int], float]:
    """Reciprocal rank fusion of the keyword and meaning rankings (module docstring)."""
    fused: dict[tuple[str, int], float] = {}
    for ranking in (_ranks(keyword), _ranks(meaning)):
        for key, rank in ranking.items():
            fused[key] = fused.get(key, 0.0) + 1.0 / (RRF_K + rank)
    return fused


def _record(
    ctx: AppContext,
    user: User,
    query: str,
    hits: list[SearchHit],
    include_drafts: bool,
    question: str | None,
) -> None:
    _audit.record(
        ctx,
        user,
        AuditKind.SEARCH,
        fail_closed=False,
        question=question,
        definitions=[f"{h.id}@{h.version}" for h in hits],
        extra={"query": query, "hits": len(hits), "include_drafts": include_drafts},
    )
