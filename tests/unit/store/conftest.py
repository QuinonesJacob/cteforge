from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from cteforge.domain.codec import CodecError, definition_from_yaml
from cteforge.domain.dialects import Dialect
from cteforge.domain.models import Definition, DefinitionState
from cteforge.store import Store

EXAMPLE = Path(__file__).resolve().parents[3] / "examples" / "definitions" / "realization_rate.yml"


@pytest.fixture(params=["memory", "file"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Store:
    url = "sqlite://" if request.param == "memory" else f"sqlite:///{tmp_path / 'cteforge.db'}"
    s = Store(url)
    s.init_schema()
    return s


@pytest.fixture
def file_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'cteforge.db'}"


@pytest.fixture
def realization_rate() -> Definition:
    try:
        return definition_from_yaml(EXAMPLE.read_text())
    except CodecError:  # The model may be changing concurrently; fall back to a built one.
        return Definition(
            id="realization_rate",
            title="Realization Rate",
            keywords=["realization", "collection rate"],
            source_dialect=Dialect.DUCKDB,
            template="SELECT 1 AS x\nFROM deals WHERE status IN ($won_statuses)",
            tables=["deals"],
            outputs=["x"],
            state=DefinitionState.LLM_ANNOTATED,
        )


@pytest.fixture
def make_def() -> Callable[..., Definition]:
    def make(id: str, version: int = 1, **extra: Any) -> Definition:
        return Definition(
            id=id,
            version=version,
            title=f"{id} v{version}",
            source_dialect=Dialect.DUCKDB,
            template=f"SELECT {version} AS v",
            **extra,
        )

    return make
