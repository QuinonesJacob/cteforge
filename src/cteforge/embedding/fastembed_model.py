"""`FastEmbedEmbedder`: `BAAI/bge-small-en-v1.5` through fastembed (ONNX on CPU, ADR 0023).

- Lazy: nothing is imported or downloaded until the first `load` / `embed`. fastembed is
  imported inside `load` (onnxruntime takes a second to import; most commands never
  embed anything).
- Cache: the model files live in `cache_dir` (services passes `CTEFORGE_EMBED_CACHE`,
  default `~/.cache/cteforge/models`). fastembed looks in the cache before the network, so
  a pre-downloaded model (`download_model`) works offline.
- Thread-safe: one load at a time (a lock); `embed` calls are serialized (one ONNX
  session); a failed load is remembered and not retried for `RETRY_AFTER_SECONDS`, so a
  server without internet does not try to download on every search.
- bge query vs passage: documents go through `passage_embed`; queries through
  `query_embed` with bge's retrieval instruction (`QUERY_INSTRUCTION`) prepended, which
  BAAI recommends for short queries against longer passages (fastembed does not add it).
- Vectors are normalized to unit length here (bge outputs already are; this makes it a
  guarantee).
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from cteforge.domain.errors import EmbeddingError
from cteforge.embedding.base import ModelStatus

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_DIM = 384
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "
RETRY_AFTER_SECONDS = 300.0
BATCH_SIZE = 32


def _unit(values: Any) -> list[float]:
    vector = [float(x) for x in values]
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector] if norm else vector


class FastEmbedEmbedder:
    """The production embedder. See the module docstring."""

    def __init__(
        self,
        cache_dir: Path,
        *,
        model_name: str = DEFAULT_MODEL,
        dim: int = DEFAULT_DIM,
        local_files_only: bool = False,
    ) -> None:
        self._cache_dir = cache_dir
        self._model_name = model_name
        self._dim = dim
        self._local_files_only = local_files_only
        self._model: Any = None
        self._load_lock = threading.Lock()
        self._embed_lock = threading.Lock()
        self._state = threading.Lock()  # Guards the fields below.
        self._loading = False
        self._error: str | None = None
        self._failed_at: float | None = None

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def cache_dir(self) -> Path:
        return self._cache_dir

    @property
    def error(self) -> str | None:
        return self._error

    def status(self) -> ModelStatus:
        with self._state:
            if self._model is not None:
                return ModelStatus.READY
            if self._loading:
                return ModelStatus.LOADING
            if self._error is not None:
                return ModelStatus.FAILED
            return ModelStatus.NOT_LOADED

    def _retry_blocked(self) -> bool:
        with self._state:
            return (
                self._failed_at is not None
                and time.monotonic() - self._failed_at < RETRY_AFTER_SECONDS
            )

    def load(self) -> None:
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            if self._retry_blocked():
                raise EmbeddingError(f"embedding model unavailable: {self._error}")
            with self._state:
                self._loading = True
            try:
                model = self._open()
            except Exception as exc:  # noqa: BLE001 — any failure means "no meaning search"
                message = f"{type(exc).__name__}: {exc}"[:300]
                with self._state:
                    self._loading = False
                    self._error = message
                    self._failed_at = time.monotonic()
                raise EmbeddingError(f"embedding model unavailable: {message}") from exc
            with self._state:
                self._model = model
                self._loading = False
                self._error = None
                self._failed_at = None

    def _open(self) -> Any:
        from fastembed import TextEmbedding  # Heavy import; only when needed.
        from loguru import logger  # fastembed's logger: its errors become EmbeddingError

        logger.disable("fastembed")

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        model = TextEmbedding(
            model_name=self._model_name,
            cache_dir=str(self._cache_dir),
            local_files_only=self._local_files_only,
        )
        size = model.embedding_size
        if size != self._dim:
            raise EmbeddingError(f"{self._model_name}: {size} dimensions, expected {self._dim}")
        return model

    def load_in_background(self) -> None:
        if self.status() in (ModelStatus.READY, ModelStatus.LOADING) or self._retry_blocked():
            return
        with self._state:
            self._loading = True  # Visible at once, before the thread starts.

        def run() -> None:
            try:
                self.load()
            except EmbeddingError:
                pass  # Recorded in `error`; callers see FAILED.

        threading.Thread(target=run, name="cteforge-embed-load", daemon=True).start()

    def embed(self, texts: Sequence[str], *, query: bool = False) -> list[list[float]]:
        if not texts:
            return []
        self.load()
        try:
            with self._embed_lock:
                if query:
                    raw = self._model.query_embed(
                        [QUERY_INSTRUCTION + t for t in texts], batch_size=BATCH_SIZE
                    )
                else:
                    raw = self._model.passage_embed(list(texts), batch_size=BATCH_SIZE)
                vectors = [_unit(v) for v in raw]
        except Exception as exc:  # noqa: BLE001 — ONNX/tokenizer failures
            raise EmbeddingError(f"embedding failed: {type(exc).__name__}") from exc
        if any(len(v) != self._dim for v in vectors):
            raise EmbeddingError("embedding has the wrong number of dimensions")
        return vectors


def download_model(cache_dir: Path, model_name: str = DEFAULT_MODEL) -> Path:
    """Download (or find in the cache) `model_name` into `cache_dir` and check that it
    loads, for servers that will run without internet access. Returns `cache_dir`.
    Raises EmbeddingError."""
    embedder = FastEmbedEmbedder(cache_dir, model_name=model_name)
    embedder.load()
    embedder.embed(["check"])
    return cache_dir
