"""HTTP API fixtures: a tmp SQLite context with a DuckDB profile on a tmp copy of
examples/warehouse.duckdb, users of every role with real tokens, and a TestClient.

The client talks to http://127.0.0.1:8765 so the MCP endpoint's Host checks (localhost
only by default) accept it.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from cteforge.api.app import create_app
from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.dialects import Dialect
from cteforge.domain.users import ActorKind, UserRole
from cteforge.services import admin
from cteforge.services.context import AppConfig, AppContext, open_context

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"
BASE_URL = "http://127.0.0.1:8765"
SQL = (EXAMPLES / "queries" / "realization_rate.sql").read_text()
ANN = yaml.safe_load((EXAMPLES / "definitions" / "realization_rate.annotations.yml").read_text())
META_KEYS = ("id", "title", "meaning", "keywords", "use_when", "dont_use_when", "grain")
Q3_APAC = {"period": {"start": "2025-07-01", "end": "2025-10-01"}, "regions": ["APAC"]}

# name → (role, kind); "root" is the bootstrap admin.
USERS = {
    "alice": (UserRole.CONTRIBUTOR, ActorKind.HUMAN),
    "ann": (UserRole.APPROVER, ActorKind.HUMAN),
    "vic": (UserRole.VIEWER, ActorKind.HUMAN),
    "bot": (UserRole.CONTRIBUTOR, ActorKind.AGENT),
    "robo": (UserRole.APPROVER, ActorKind.AGENT),
}
ENV_KEYS = ("CTEFORGE_WEB_DIST", "CTEFORGE_BIND_HOST", "CTEFORGE_AUTH", "CTEFORGE_USER")


@dataclass
class Api:
    ctx: AppContext
    client: TestClient
    tokens: dict[str, str]

    def h(self, name: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens[name]}"}

    def get(self, path: str, user: str, **kw: Any) -> httpx.Response:
        return self.client.get(f"/api/v1{path}", headers=self.h(user), **kw)

    def post(self, path: str, user: str, body: Any = None, **kw: Any) -> httpx.Response:
        return self.client.post(f"/api/v1{path}", headers=self.h(user), json=body, **kw)

    def put(self, path: str, user: str, body: Any = None) -> httpx.Response:
        return self.client.put(f"/api/v1{path}", headers=self.h(user), json=body)

    def patch(self, path: str, user: str, body: Any = None) -> httpx.Response:
        return self.client.patch(f"/api/v1{path}", headers=self.h(user), json=body)

    def delete(self, path: str, user: str) -> httpx.Response:
        return self.client.delete(f"/api/v1{path}", headers=self.h(user))

    def propose_rr(self, user: str = "alice", **meta: Any) -> httpx.Response:
        return self.post("/proposals/definitions", user, proposal_body(**meta))

    @staticmethod
    def proposal_body(**meta: Any) -> dict[str, Any]:
        return proposal_body(**meta)


def proposal_body(**meta: Any) -> dict[str, Any]:
    return {
        "sql": SQL,
        "dialect": "duckdb",
        "metadata": {k: ANN[k] for k in META_KEYS} | meta,
        "annotations": [dict(a) for a in ANN["variables"]],
        "summary": "realization rate",
    }


def error_of(response: httpx.Response, status: int, type_: str) -> dict[str, Any]:
    """The {"error": {...}} body, checked for status, type and shape (no traceback)."""
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}
    error = body["error"]
    assert error["type"] == type_
    assert isinstance(error["message"], str) and isinstance(error["issues"], list)
    assert "Traceback" not in response.text and 'File "' not in response.text
    return error


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(scope="session")
def warehouse_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("api_wh") / "warehouse.duckdb"
    shutil.copy(EXAMPLES / "warehouse.duckdb", path)
    return path


def make_context(tmp_path: Path, warehouse_file: Path, **config: Any) -> AppContext:
    broken = tmp_path / "broken.duckdb"
    broken.write_text("not a database")
    profiles = [
        WarehouseProfile(
            name="demo", dialect=Dialect.DUCKDB, options={"path": str(warehouse_file)}
        ),
        WarehouseProfile(name="broken", dialect=Dialect.DUCKDB, options={"path": str(broken)}),
    ]
    settings = {"db_url": f"sqlite:///{tmp_path / 'cteforge.db'}", "default_profile": "demo"}
    return open_context(AppConfig(**(settings | config)), profiles=profiles)


@pytest.fixture
def check_error():
    """`error_of` as a fixture (test modules import nothing from conftest)."""
    return error_of


@pytest.fixture
def new_context(tmp_path: Path, warehouse_file: Path):
    """Factory: another context on its own tmp SQLite db (config overrides as kwargs)."""
    opened: list[AppContext] = []

    def factory(**config: Any) -> AppContext:
        sub = tmp_path / f"ctx{len(opened)}"
        sub.mkdir()
        opened.append(make_context(sub, warehouse_file, **config))
        return opened[-1]

    yield factory
    for context in opened:
        context.close()


@pytest.fixture
def ctx(tmp_path: Path, warehouse_file: Path) -> Iterator[AppContext]:
    with make_context(tmp_path, warehouse_file) as context:
        yield context


@pytest.fixture
def api(ctx: AppContext, tmp_path: Path) -> Iterator[Api]:
    root, root_token = admin.bootstrap_admin(ctx, "root")
    tokens = {"root": root_token}
    for name, (role, kind) in USERS.items():
        _, tokens[name] = admin.create_user(ctx, root, name, role, kind)
    app = create_app(ctx, web_dist=tmp_path / "no-dist")
    with TestClient(app, base_url=BASE_URL, raise_server_exceptions=False) as client:
        yield Api(ctx=ctx, client=client, tokens=tokens)
