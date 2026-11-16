"""Static GUI (SPA fallback, no path traversal), security headers, and error bodies that
never leak tracebacks or exception details."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cteforge.api.app import REPO_WEB_DIST, create_app, resolve_web_dist
from cteforge.services import search

BASE_URL = "http://127.0.0.1:8765"
INDEX = "<!doctype html><title>cteforge test shell</title><div id=root></div>"


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    root = tmp_path / "site"
    (root / "web" / "dist" / "assets").mkdir(parents=True)
    dist = root / "web" / "dist"
    (dist / "index.html").write_text(INDEX)
    (dist / "assets" / "app.js").write_text("console.log('app')")
    (root / "pyproject.toml").write_text("[project]\nname = 'secret-outside-dist'\n")
    (root / "web" / "secret.txt").write_text("outside dist")
    return dist


@pytest.fixture
def gui(ctx, dist: Path):
    with TestClient(create_app(ctx, web_dist=dist), base_url=BASE_URL) as client:
        yield client


def test_index_and_assets(gui) -> None:
    index = gui.get("/")
    assert index.status_code == 200 and index.text == INDEX
    assert index.headers["content-type"].startswith("text/html")
    asset = gui.get("/assets/app.js")
    assert asset.status_code == 200 and asset.text == "console.log('app')"
    assert "immutable" in asset.headers["cache-control"]


@pytest.mark.parametrize("path", ["/library", "/definitions/realization_rate", "/review/x"])
def test_spa_fallback(gui, path: str) -> None:
    response = gui.get(path)
    assert response.status_code == 200 and response.text == INDEX


def test_missing_asset_is_404_not_shell(gui, check_error) -> None:
    check_error(gui.get("/assets/missing.js"), 404, "HTTPError")


@pytest.mark.parametrize(
    "path",
    [
        "/..%2f..%2fpyproject.toml",
        "/..%2fsecret.txt",
        "/assets/..%2f..%2f..%2fpyproject.toml",
        "/%2e%2e/%2e%2e/pyproject.toml",
        "/..%5c..%5cpyproject.toml",
        "/%00",
    ],
)
def test_no_path_traversal(gui, path: str) -> None:
    response = gui.get(path)
    assert "secret-outside-dist" not in response.text
    assert "outside dist" not in response.text
    assert response.status_code in (200, 404)
    if response.status_code == 200:
        assert response.text == INDEX


def test_no_traversal_from_repo_dist(ctx, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real web/dist next to the real pyproject.toml (the default location)."""
    if resolve_web_dist() is None:
        pytest.skip("web/dist is not built")
    assert resolve_web_dist() == REPO_WEB_DIST.resolve()
    with TestClient(create_app(ctx), base_url=BASE_URL) as client:
        response = client.get("/..%2f..%2fpyproject.toml")
        assert "[tool.importlinter]" not in response.text
        assert response.status_code == 200 and '<div id="root">' in response.text


def test_api_and_mcp_paths_never_fall_back(gui, check_error) -> None:
    check_error(gui.get("/api/v1/unknown"), 404, "HTTPError")
    check_error(gui.get("/api"), 404, "HTTPError")


def test_web_dist_from_env(ctx, dist: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CTEFORGE_WEB_DIST", str(dist))
    assert resolve_web_dist() == dist.resolve()
    with TestClient(create_app(ctx), base_url=BASE_URL) as client:
        assert client.get("/").text == INDEX


def test_missing_dist_gives_json_hint(api, check_error) -> None:
    response = api.client.get("/")
    assert response.status_code == 200
    assert response.json()["api"] == "/api/v1" and "gui" in response.json()
    check_error(api.client.get("/library"), 404, "HTTPError")


def test_security_headers(gui, api) -> None:
    for response in (gui.get("/"), gui.get("/assets/app.js"), api.get("/me", "vic")):
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert "default-src 'self'" in response.headers["content-security-policy"]
        assert response.headers["x-frame-options"] == "DENY"
    assert api.get("/me", "vic").headers["cache-control"] == "no-store"
    error = api.client.get("/api/v1/me")
    assert error.headers["x-content-type-options"] == "nosniff"


def test_no_cors_headers(api) -> None:
    response = api.client.options(
        "/api/v1/me",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in response.headers


def test_openapi_is_served_without_docs_ui(api) -> None:
    spec = api.client.get("/api/v1/openapi.json")
    assert spec.status_code == 200
    assert "/api/v1/proposals/count" in spec.json()["paths"]
    assert api.client.get("/docs").status_code in (200, 404)  # SPA hint/404, never Swagger
    assert "swagger" not in api.client.get("/docs").text.lower()


def test_unexpected_error_is_generic_500(
    api, monkeypatch: pytest.MonkeyPatch, check_error, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(*args, **kwargs):
        raise RuntimeError("postgresql://admin:hunter2@db/prod exploded")

    monkeypatch.setattr(search, "search", boom)
    with caplog.at_level("ERROR", logger="cteforge.api"):
        response = api.get("/definitions", "vic")
    error = check_error(response, 500, "InternalError")
    assert error == {"type": "InternalError", "message": "internal error", "issues": []}
    assert "hunter2" not in response.text and "RuntimeError" not in response.text
    assert "RuntimeError" in caplog.text  # logged server-side...
    assert "hunter2" not in caplog.text  # ...without the message


def test_error_bodies_have_no_traceback(api) -> None:
    responses = [
        api.client.get("/api/v1/me"),
        api.get("/definitions/nope", "vic"),
        api.post("/parse", "vic", {"sql": "SELECT FROM WHERE (", "dialect": "duckdb"}),
        api.post("/parse", "vic", {"nope": 1}),
        api.get("/audit", "vic"),
    ]
    for response in responses:
        assert response.status_code >= 400
        assert "Traceback" not in response.text and ".py" not in response.text
