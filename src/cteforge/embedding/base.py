"""The `Embedder` protocol services code against (ADR 0023)."""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Protocol, runtime_checkable


class ModelStatus(StrEnum):
    NOT_LOADED = "not_loaded"  # Nothing tried yet (lazy).
    LOADING = "loading"  # A load (maybe a first download) is running.
    READY = "ready"
    FAILED = "failed"  # The last load failed; `error` says why (no retry until later).


@runtime_checkable
class Embedder(Protocol):
    """Text → vector. Vectors are unit length, so a dot product is the cosine similarity.

    `embed(texts)` embeds documents (passages); `embed(texts, query=True)` embeds search
    queries — models like bge treat the two differently. Either call loads the model
    first when needed (blocking) and raises EmbeddingError when it can't."""

    @property
    def model_name(self) -> str: ...

    @property
    def dim(self) -> int: ...

    def status(self) -> ModelStatus: ...

    @property
    def error(self) -> str | None: ...

    def load(self) -> None:
        """Load the model now (download it on first use). Raises EmbeddingError."""
        ...

    def load_in_background(self) -> None:
        """Start loading in a daemon thread, unless loading, loaded or recently failed."""
        ...

    def embed(self, texts: Sequence[str], *, query: bool = False) -> list[list[float]]: ...
