"""embedding.fastembed_model: lazy, thread-safe, query vs passage — with a fake fastembed
model (tests never load the real one; conftest refuses `_open`)."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from cteforge.domain.errors import EmbeddingError
from cteforge.embedding import fastembed_model as fm
from cteforge.embedding.base import Embedder, ModelStatus
from cteforge.embedding.fastembed_model import FastEmbedEmbedder


class FakeTextEmbedding:
    """What `TextEmbedding` gives back: passage_embed / query_embed yielding arrays."""

    def __init__(self, dim: int = 3) -> None:
        self.dim = dim
        self.passages: list[str] = []
        self.queries: list[str] = []

    def passage_embed(self, texts, batch_size=32):
        self.passages.extend(texts)
        return iter([[3.0, 4.0] + [0.0] * (self.dim - 2) for _ in texts])

    def query_embed(self, texts, batch_size=32):
        self.queries.extend(texts)
        return iter([[0.0, 0.0, 2.0] + [0.0] * (self.dim - 3) for _ in texts])


def embedder(monkeypatch, tmp_path: Path, model=None, opens: list | None = None, dim=3):
    model = model or FakeTextEmbedding(dim)

    def _open(self):
        if opens is not None:
            opens.append(1)
            time.sleep(0.05)
        return model

    monkeypatch.setattr(FastEmbedEmbedder, "_open", _open)
    return FastEmbedEmbedder(tmp_path / "models", dim=dim), model


def test_protocol_and_lazy(monkeypatch, tmp_path: Path) -> None:
    e, _ = embedder(monkeypatch, tmp_path)
    assert isinstance(e, Embedder)
    assert e.model_name == fm.DEFAULT_MODEL and e.dim == 3
    assert e.status() is ModelStatus.NOT_LOADED  # Nothing loaded at construction.
    e.load()
    assert e.status() is ModelStatus.READY


def test_passages_and_queries_differ_and_are_unit_length(monkeypatch, tmp_path: Path) -> None:
    e, model = embedder(monkeypatch, tmp_path)
    assert e.embed(["doc one", "doc two"]) == [[0.6, 0.8, 0.0], [0.6, 0.8, 0.0]]
    assert e.embed(["what is x"], query=True) == [[0.0, 0.0, 1.0]]
    assert model.passages == ["doc one", "doc two"]
    assert model.queries == [fm.QUERY_INSTRUCTION + "what is x"]  # bge retrieval prompt
    assert e.embed([]) == []


def test_wrong_dimension_is_an_error(monkeypatch, tmp_path: Path) -> None:
    e, _ = embedder(monkeypatch, tmp_path, model=FakeTextEmbedding(4))
    with pytest.raises(EmbeddingError, match="dimensions"):
        e.embed(["x"])


def test_failed_load_is_remembered(monkeypatch, tmp_path: Path) -> None:
    calls = []

    def _open(self):
        calls.append(1)
        raise OSError("no internet")

    monkeypatch.setattr(FastEmbedEmbedder, "_open", _open)
    e = FastEmbedEmbedder(tmp_path / "models")
    with pytest.raises(EmbeddingError, match="no internet"):
        e.load()
    assert e.status() is ModelStatus.FAILED and "no internet" in (e.error or "")
    with pytest.raises(EmbeddingError):
        e.embed(["x"])  # Not retried within RETRY_AFTER_SECONDS.
    e.load_in_background()  # Also not retried in the background.
    assert len(calls) == 1
    monkeypatch.setattr(fm, "RETRY_AFTER_SECONDS", 0.0)
    with pytest.raises(EmbeddingError):
        e.load()
    assert len(calls) == 2


def test_concurrent_loads_open_once(monkeypatch, tmp_path: Path) -> None:
    opens: list[int] = []
    e, _ = embedder(monkeypatch, tmp_path, opens=opens)
    threads = [threading.Thread(target=e.embed, args=(["x"],)) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len(opens) == 1 and e.status() is ModelStatus.READY


def test_background_load(monkeypatch, tmp_path: Path) -> None:
    opens: list[int] = []
    e, _ = embedder(monkeypatch, tmp_path, opens=opens)
    e.load_in_background()
    assert e.status() in (ModelStatus.LOADING, ModelStatus.READY)
    deadline = time.monotonic() + 5
    while e.status() is not ModelStatus.READY and time.monotonic() < deadline:
        time.sleep(0.01)
    assert e.status() is ModelStatus.READY
    e.load_in_background()  # Already loaded: nothing more.
    assert len(opens) == 1


def test_download_model_loads_and_checks(monkeypatch, tmp_path: Path) -> None:
    model = FakeTextEmbedding(fm.DEFAULT_DIM)
    monkeypatch.setattr(FastEmbedEmbedder, "_open", lambda self: model)
    assert fm.download_model(tmp_path / "cache") == tmp_path / "cache"
    assert model.passages == ["check"]


def test_real_model_is_refused_in_tests(tmp_path: Path) -> None:
    """The conftest guard: no test can open the real model (no download, no network)."""
    with pytest.raises(EmbeddingError, match="tests never load"):
        FastEmbedEmbedder(tmp_path / "models").load()
