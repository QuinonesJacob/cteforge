from __future__ import annotations

import hashlib
import importlib.util
import math
import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from cteforge.domain.codec import AnnotationFile, annotations_from_yaml
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import EmbeddingError
from cteforge.domain.models import Definition
from cteforge.embedding.base import ModelStatus
from cteforge.embedding.fastembed_model import FastEmbedEmbedder
from cteforge.parsing.analyze import analyze
from cteforge.services import context as context_module
from cteforge.templating.template import apply_annotations

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"

# Tests never download or load the real embedding model (ADR 0023). Subprocesses inherit
# these; the e2e tests also set CTEFORGE_SEARCH_EMBEDDINGS=off for the servers they start.
os.environ["CTEFORGE_SEARCH_EMBEDDINGS"] = "off"
os.environ["HF_HUB_OFFLINE"] = "1"


def _refuse_model(self: FastEmbedEmbedder) -> None:
    raise EmbeddingError("tests never load the real embedding model")


@pytest.fixture(autouse=True)
def _no_real_embedding_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Contexts get no embedder (keyword search) unless a test passes one, and the real
    fastembed model can never be opened."""
    monkeypatch.setattr(context_module, "default_embedder", lambda config: None)
    monkeypatch.setattr(FastEmbedEmbedder, "_open", _refuse_model)


class FakeEmbedder:
    """Deterministic stand-in for the real model (implements embedding.base.Embedder).

    With `topics` (topic → trigger words), a text's vector counts the trigger words of each
    topic, unit length: two texts "mean" the same when they hit the same topics, with no
    words in common needed. Without `topics`, a bag of words hashed into `dim` buckets.
    Records calls so tests can check what was embedded."""

    def __init__(
        self,
        topics: Mapping[str, Sequence[str]] | None = None,
        *,
        dim: int = 64,
        model_name: str = "fake/bag-of-words",
        ready: bool = True,
        fail: bool = False,
    ) -> None:
        self.topics = {name: {w.lower() for w in words} for name, words in (topics or {}).items()}
        self._dim = len(self.topics) if self.topics else dim
        self._model_name = model_name
        self._ready = ready
        self.fail = fail
        self.calls: list[tuple[tuple[str, ...], bool]] = []
        self.loads = 0
        self.background_loads = 0
        self.loading = False

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def error(self) -> str | None:
        return "fake failure" if self.fail else None

    def status(self) -> ModelStatus:
        if self.fail:
            return ModelStatus.FAILED
        if self._ready:
            return ModelStatus.READY
        return ModelStatus.LOADING if self.loading else ModelStatus.NOT_LOADED

    def load(self) -> None:
        self.loads += 1
        if self.fail:
            raise EmbeddingError("embedding model unavailable: fake failure")
        self._ready = True

    def load_in_background(self) -> None:
        """Pretends a load started and never finishes (tests flip `_ready` themselves)."""
        self.background_loads += 1
        self.loading = not self.fail

    def vector(self, text: str) -> list[float]:
        words = re.findall(r"[a-z0-9]+", text.lower())
        out = [0.0] * self._dim
        if self.topics:
            for i, triggers in enumerate(self.topics.values()):
                out[i] = float(sum(1 for w in words if w in triggers))
        else:
            for word in words:
                out[int(hashlib.sha256(word.encode()).hexdigest(), 16) % self._dim] += 1.0
        norm = math.sqrt(sum(x * x for x in out))
        return [x / norm for x in out] if norm else out

    def embed(self, texts: Sequence[str], *, query: bool = False) -> list[list[float]]:
        self.load()
        self.calls.append((tuple(texts), query))
        return [self.vector(t) for t in texts]


@pytest.fixture
def fake_embedder_class() -> type[FakeEmbedder]:
    """The FakeEmbedder class (construct it with the topics a test needs)."""
    return FakeEmbedder


@pytest.fixture(scope="session")
def sample_sql() -> str:
    return (EXAMPLES / "queries" / "realization_rate.sql").read_text()


@pytest.fixture(scope="session")
def sample_annotations() -> AnnotationFile:
    return annotations_from_yaml(
        (EXAMPLES / "definitions" / "realization_rate.annotations.yml").read_text()
    )


@pytest.fixture(scope="session")
def warehouse_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    spec = importlib.util.spec_from_file_location("seed", EXAMPLES / "seed_warehouse.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = tmp_path_factory.mktemp("wh") / "warehouse.duckdb"
    module.seed(path)
    return path


@pytest.fixture(scope="session")
def warehouse(warehouse_path: Path):
    from cteforge.warehouse.duckdb import DuckDBWarehouse

    wh = DuckDBWarehouse(warehouse_path)
    yield wh
    wh.close()


# Static copy of the seeded warehouse schema so unit tests need no database.
# tests/integration/test_import_and_render.py asserts it matches the real warehouse.
SAMPLE_SCHEMA = {
    "accounts": {"account_id": "INTEGER", "name": "VARCHAR", "region": "VARCHAR"},
    "deals": {
        "deal_id": "INTEGER",
        "account_id": "INTEGER",
        "closed_at": "TIMESTAMP",
        "status": "VARCHAR",
        "amount": "DECIMAL(12,2)",
    },
    "invoices": {
        "invoice_id": "INTEGER",
        "deal_id": "INTEGER",
        "invoiced_at": "TIMESTAMP",
        "status": "VARCHAR",
        "paid_amount": "DECIMAL(12,2)",
    },
}


@pytest.fixture(scope="session")
def sample_schema() -> dict[str, dict[str, str]]:
    return SAMPLE_SCHEMA


def build_definition(sql: str, annotations: AnnotationFile, schema: dict) -> Definition:
    """Definition from an example query + annotations, typed with a static schema."""
    analysis = analyze(sql, Dialect.DUCKDB, schema)
    annotated = apply_annotations(sql, Dialect.DUCKDB, analysis, annotations.variables)
    return Definition(
        id=annotations.id,
        title=annotations.title,
        source_dialect=Dialect.DUCKDB,
        template=annotated.template,
        variables=annotated.variables,
        tables=analysis.tables,
        outputs=analysis.outputs,
    )


@pytest.fixture(scope="session")
def sample_definition(sample_sql, sample_annotations, sample_schema) -> Definition:
    return build_definition(sample_sql, sample_annotations, sample_schema)


@pytest.fixture(scope="session")
def collections_sql() -> str:
    """Second example: multi-place `period`, strptime-wrapped dates, number with a unit."""
    return (EXAMPLES / "queries" / "booked_vs_collected.sql").read_text()


@pytest.fixture(scope="session")
def collections_annotations() -> AnnotationFile:
    return annotations_from_yaml(
        (EXAMPLES / "definitions" / "booked_vs_collected.annotations.yml").read_text()
    )


@pytest.fixture(scope="session")
def collections_definition(collections_sql, collections_annotations, sample_schema) -> Definition:
    return build_definition(collections_sql, collections_annotations, sample_schema)
