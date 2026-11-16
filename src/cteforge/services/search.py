"""Use-case: search the library (DESIGN §19, ADR 0017).

Lexical BM25F-style scoring, pure Python over the store's definitions:
- Fields and weights: title 3, keywords 3, meaning 2, use_when 1, variable names and
  descriptions 1, outputs 1, tables 1, dont_use_when 0.5 (it says when NOT to use it, so
  a match there is weaker evidence).
- Tokenizer: lowercase, split on anything that is not a letter or digit (so snake_case
  splits), drop a few English stopwords, light plural stripping (`rates` → `rate`,
  `categories` → `category`, `classes` → `class`; words ending in `ss`/`us`/`is` kept).
- Ranking: hits whose only matches are in `dont_use_when` come last (the words name what
  the definition is NOT for); then score desc; at equal score (6 decimals) verified before
  llm_annotated before draft; then title, then id. A non-empty query only returns
  definitions that match.
- Each hit carries `use_when` / `dont_use_when` / `grain` and `matched_terms` /
  `matched_fields`, so an agent can judge a hit without fetching its card. Scores are BM25
  with no fixed scale: comparable within one search only.
- Empty query (no tokens) → every visible definition, ordered by title, score 0.

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
from dataclasses import dataclass

from cteforge.domain.audit import AuditKind
from cteforge.domain.models import Definition, DefinitionState
from cteforge.domain.users import User
from cteforge.services import _audit, _library
from cteforge.services.auth import require
from cteforge.services.cards import SearchHit, search_hit
from cteforge.services.context import AppContext, team_mode
from cteforge.trust.approval import Action

MAX_LIMIT = 100
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


def _fields(d: Definition) -> dict[str, list[str]]:
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


def _doc(d: Definition) -> _Doc:
    tf: Counter[str] = Counter()
    length = 0.0
    fields = _fields(d)
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
    1..100. Needs Action.SEARCH (viewer)."""
    with _audit.audit_failures(ctx, user, AuditKind.SEARCH, question=question):
        require(user, Action.SEARCH)
    limit = max(1, min(limit, MAX_LIMIT))
    states = _library.visible_states(team_mode(ctx), include_drafts)
    candidates = [
        found
        for latest in ctx.store.list_definitions()
        if (found := _library.newest_in_states(ctx, latest, states)) is not None
    ]
    terms = tokenize(query or "")
    docs = [_doc(d) for d in candidates]
    if terms:
        scored = [
            (score, doc) for score, doc in zip(bm25(terms, docs), docs, strict=True) if score > 0
        ]
    else:
        scored = [(0.0, doc) for doc in docs]
    scored.sort(
        key=lambda item: (
            # Matching only where the definition says NOT to use it is no evidence for it.
            item[1].only_dont_use_when(terms),
            -round(item[0], 6),
            STATE_RANK[item[1].definition.state],
            item[1].definition.title.lower(),
            item[1].definition.id,
        )
    )
    hits = []
    for score, doc in scored[:limit]:
        found, fields = doc.matches(terms)
        hits.append(
            search_hit(doc.definition, round(score, 6), matched_terms=found, matched_fields=fields)
        )
    _audit.record(
        ctx,
        user,
        AuditKind.SEARCH,
        fail_closed=False,
        question=question,
        definitions=[f"{h.id}@{h.version}" for h in hits],
        extra={"query": query, "hits": len(hits), "include_drafts": include_drafts},
    )
    return hits
