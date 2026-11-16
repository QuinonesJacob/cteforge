"""End-to-end fixtures: the product wired together (API + GUI contract, real server, MCP).

`e2e` marks tests that start real processes (cteforge demo / serve / mcp); they are slower
but need nothing beyond the repo (DuckDB warehouse seeded in a tmp dir).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"
WEB_DIST = ROOT / "web" / "dist"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "e2e: starts real cteforge processes (demo, serve, mcp)")


@pytest.fixture(scope="session")
def example():
    """Loader: name → (SQL text, annotation file as a dict) of an example query."""

    def load(name: str) -> tuple[str, dict]:
        sql = (EXAMPLES / "queries" / f"{name}.sql").read_text()
        ann = yaml.safe_load((EXAMPLES / "definitions" / f"{name}.annotations.yml").read_text())
        return sql, ann

    return load
