"""Use-case: maintain the meaning-search index (DESIGN §19, ADR 0023).

- `rebuild_search_index(ctx, user, *, force=False)` — admin (MANAGE_SETTINGS), audited
  (admin, op=rebuild_search_index, closed): loads the model (downloading it on first use),
  deletes vectors of other models (and, with `force`, every vector), then embeds every
  version search can show whose vector is missing or whose search document changed.
- `download_model(config)` — operator tooling with no store and no user (like the M1
  file commands): put the model in `config.embed_cache_dir` so a server without internet
  access can load it later.
- `start_background(ctx)` — for long-running servers (`serve`, stdio `mcp`): requests
  never wait for the model; a daemon thread loads it and backfills vectors.
"""

from __future__ import annotations

import threading
from pathlib import Path

from cteforge.domain.audit import AuditKind
from cteforge.domain.errors import PolicyError
from cteforge.domain.users import User
from cteforge.embedding.fastembed_model import download_model as _download
from cteforge.services import _audit, _meaning
from cteforge.services.auth import require
from cteforge.services.cards import SearchIndexReport
from cteforge.services.context import AppConfig, AppContext
from cteforge.trust.approval import Action

OFF_MESSAGE = "Meaning search is off (CTEFORGE_SEARCH_EMBEDDINGS=off); nothing to index"


def rebuild_search_index(ctx: AppContext, user: User, *, force: bool = False) -> SearchIndexReport:
    """See the module docstring. PolicyError when meaning search is off; EmbeddingError
    when the model cannot be loaded."""
    fields = {"extra": {"op": "rebuild_search_index", "force": force}}
    with _audit.audit_failures(ctx, user, AuditKind.ADMIN, **fields):
        require(user, Action.MANAGE_SETTINGS)
        embedder = ctx.embedder
        if embedder is None:
            raise PolicyError(OFF_MESSAGE)
        embedder.load()
        removed = 0
        if force:
            removed = ctx.store.delete_search_vectors()
        else:
            for model in _other_models(ctx, embedder.model_name):
                removed += ctx.store.delete_search_vectors(model)
        versions = _meaning.indexable(ctx)
        counts = _meaning.ensure_vectors(ctx, versions, check_documents=True)
    report = SearchIndexReport(
        model=embedder.model_name,
        definitions=len(versions),
        embedded=counts.embedded,
        unchanged=counts.unchanged,
        removed=removed,
    )
    _audit.record(
        ctx, user, AuditKind.ADMIN, extra={**fields["extra"], "embedded": report.embedded}
    )
    return report


def _other_models(ctx: AppContext, current: str) -> list[str]:
    return [m for m in ctx.store.search_vector_models() if m != current]


def download_model(config: AppConfig) -> Path:
    """Download (or find) the embedding model in `config.embed_cache_dir` and check that
    it loads. Returns the directory. Raises EmbeddingError."""
    return _download(config.embed_cache_dir)


def start_background(ctx: AppContext) -> threading.Thread | None:
    """See the module docstring. None when meaning search is off."""
    return _meaning.start_background(ctx)
