"""Bearer auth, CTEFORGE_AUTH=off on loopback only, and the local-only guards (ADR 0018)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from cteforge.api.app import create_app
from cteforge.api.auth import bearer_token, is_loopback
from cteforge.domain.errors import PolicyError
from cteforge.domain.users import ActorKind, UserRole
from cteforge.services.context import AuthMode

BASE_URL = "http://127.0.0.1:8765"


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer "},
        {"Authorization": "Bearer not-a-real-token"},
        {"Authorization": "Basic YWxpY2U6cHc="},
    ],
)
def test_missing_or_invalid_token_is_401(api, headers, check_error) -> None:
    response = api.client.get("/api/v1/me", headers=headers)
    error = check_error(response, 401, "Unauthorized")
    assert error["message"] == "Invalid or missing token"
    assert response.headers["www-authenticate"].startswith("Bearer")


def test_401_comes_before_body_validation(api, check_error) -> None:
    response = api.client.post("/api/v1/parse", json={"bogus": 1})
    check_error(response, 401, "Unauthorized")


def test_valid_token_resolves_user(api) -> None:
    response = api.get("/me", "bot")
    assert response.status_code == 200
    assert response.json()["name"] == "bot"
    assert response.json()["role"] == UserRole.CONTRIBUTOR.value
    assert response.json()["kind"] == ActorKind.AGENT.value


def test_bearer_scheme_is_case_insensitive(api) -> None:
    response = api.client.get(
        "/api/v1/me", headers={"Authorization": f"bearer {api.tokens['vic']}"}
    )
    assert response.status_code == 200 and response.json()["name"] == "vic"


def test_bearer_token_parsing() -> None:
    assert bearer_token("Bearer abc") == "abc"
    assert bearer_token("  Bearer   abc ") == "abc"
    assert bearer_token("Token abc") is None
    assert bearer_token("Bearer") is None
    assert bearer_token(None) is None


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True),
        ("127.0.0.2", True),
        ("::1", True),
        ("[::1]", True),
        ("localhost", True),
        ("LOCALHOST", True),
        ("0.0.0.0", False),
        ("192.168.1.10", False),
        ("example.com", False),
        ("localhost.example.com", False),
    ],
)
def test_is_loopback(host: str, expected: bool) -> None:
    assert is_loopback(host) is expected


# ── auth off ─────────────────────────────────────────────────────────────────────────

LOCAL_PEER = ("127.0.0.1", 50000)  # The TCP peer of an auth-off request (ASGI scope client).


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "example.com"])
def test_auth_off_refused_off_loopback(new_context, host: str) -> None:
    with new_context(auth=AuthMode.OFF) as ctx:
        with pytest.raises(PolicyError, match="CTEFORGE_AUTH=off"):
            create_app(ctx, host=host)


def test_auth_off_refused_for_env_bind_host(new_context, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CTEFORGE_BIND_HOST", "0.0.0.0")
    with new_context(auth=AuthMode.OFF) as ctx:
        with pytest.raises(PolicyError):
            create_app(ctx)


def test_auth_on_is_allowed_off_loopback(new_context) -> None:
    with new_context() as ctx:
        assert create_app(ctx, host="0.0.0.0") is not None


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_auth_off_on_loopback_acts_as_local_admin(new_context, tmp_path, host: str) -> None:
    with new_context(auth=AuthMode.OFF, user="me") as ctx:
        app = create_app(ctx, host=host, web_dist=tmp_path / "none")
        with TestClient(app, base_url=BASE_URL, client=LOCAL_PEER) as client:
            response = client.get("/api/v1/me")  # no token at all
            assert response.status_code == 200
            assert response.json()["name"] == "me"
            assert response.json()["role"] == "admin"
            assert client.get("/api/v1/settings").json()["mode"] == "open"


def test_auth_off_rejects_foreign_host_and_origin(new_context, tmp_path, check_error) -> None:
    with new_context(auth=AuthMode.OFF) as ctx:
        app = create_app(ctx, web_dist=tmp_path / "none")
        with TestClient(app, base_url=BASE_URL, client=LOCAL_PEER) as client:
            # A local connection naming a foreign Host (DNS rebinding).
            response = client.get("/api/v1/me", headers={"Host": "evil.example:8765"})
            check_error(response, 421, "MisdirectedRequest")
        with TestClient(app, base_url=BASE_URL, client=LOCAL_PEER) as client:
            response = client.post(
                "/api/v1/users/me/token", headers={"Origin": "https://evil.example"}
            )
            check_error(response, 403, "PermissionDeniedError")
            ok = client.get("/api/v1/me", headers={"Origin": "http://localhost:5173"})
            assert ok.status_code == 200


def test_auth_off_with_unknown_local_user_is_403(new_context, tmp_path, check_error) -> None:
    from cteforge.services import admin

    with new_context(auth=AuthMode.OFF, user="ghost") as ctx:
        admin.bootstrap_admin(ctx, "root")  # users exist, "ghost" is not one of them
        app = create_app(ctx, web_dist=tmp_path / "none")
        with TestClient(app, base_url=BASE_URL, client=LOCAL_PEER) as client:
            check_error(client.get("/api/v1/me"), 403, "PermissionDeniedError")


def test_new_token_works_and_revoking_the_old_one_stops_it(api, check_error) -> None:
    old = api.tokens["vic"]
    response = api.post("/me/tokens", "vic", {"name": "new laptop"})
    assert response.status_code == 201
    new = response.json()["token"]
    assert new != old and new.startswith("cfg_pat_")
    [default] = [t for t in api.get("/me/tokens", "vic").json() if t["name"] == "default"]
    assert response.json()["info"]["parent_token_id"] == default["id"]  # minted by it
    headers = {"Authorization": f"Bearer {new}"}
    assert api.client.get("/api/v1/me", headers=headers).json()["name"] == "vic"
    # A token minted by a token is revoked with it (security review: no persistence).
    api.client.delete(f"/api/v1/me/tokens/{default['id']}", headers=headers)
    for token in (old, new):
        check_error(
            api.client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"}),
            401,
            "Unauthorized",
        )
    # One generated outside any token (the CLI, or an admin for this user) is independent.
    made = api.post("/users/alice/tokens", "root", {"name": "laptop"}).json()
    assert made["info"]["parent_token_id"] is None
    [alice_default] = [t for t in api.get("/me/tokens", "alice").json() if t["name"] == "default"]
    api.delete(f"/me/tokens/{alice_default['id']}", "alice")
    laptop = {"Authorization": f"Bearer {made['token']}"}
    assert api.client.get("/api/v1/me", headers=laptop).status_code == 200


def test_poc1_a_short_lived_narrow_token_cannot_leave_a_lasting_one(api, check_error) -> None:
    """Regression (security review, poc1): an agent's 1-hour `only: [search]` token minted
    a token without expiry for its own user that survived revoking the original."""
    import datetime as dt

    hour = (dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)).isoformat()
    made = api.post(
        "/users/alice/tokens",
        "root",
        {"name": "short", "expires_at": hour, "tool_mode": "only", "include": ["search"]},
    ).json()
    short = {"Authorization": f"Bearer {made['token']}"}
    me = api.client.get("/api/v1/me", headers=short).json()
    assert me["token_id"] == made["info"]["id"] and me["token_mode"] == "only"
    assert me["token_expires_at"] is not None

    def mint(body: dict) -> object:
        return api.client.post("/api/v1/me/tokens", headers=short, json=body)

    only_search = {"tool_mode": "only", "include": ["search"]}
    check_error(mint({"name": "forever"} | only_search), 409, "PolicyError")
    later = (dt.datetime.now(dt.UTC) + dt.timedelta(days=30)).isoformat()
    check_error(mint({"name": "later", "expires_at": later} | only_search), 409, "PolicyError")
    check_error(mint({"name": "grows", "expires_at": hour}), 409, "PolicyError")  # follow_role
    soon = (dt.datetime.now(dt.UTC) + dt.timedelta(minutes=30)).isoformat()
    child = mint({"name": "child", "expires_at": soon} | only_search)
    assert child.status_code == 201
    child_headers = {"Authorization": f"Bearer {child.json()['token']}"}
    # Revoking the original revokes what it minted.
    assert api.delete(f"/users/alice/tokens/{made['info']['id']}", "root").status_code == 200
    check_error(api.client.get("/api/v1/me", headers=child_headers), 401, "Unauthorized")
    states = {t["name"]: t["state"] for t in api.get("/me/tokens", "alice").json()}
    assert states["short"] == states["child"] == "revoked"


def test_factory_without_ctx_opens_and_closes_context(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """uvicorn's factory call: config from the environment, context owned by the lifespan."""
    monkeypatch.setenv("CTEFORGE_DB_URL", f"sqlite:///{tmp_path / 'env.db'}")
    monkeypatch.setenv("CTEFORGE_AUTH", "off")
    monkeypatch.setenv("CTEFORGE_USER", "envuser")
    monkeypatch.delenv("CTEFORGE_CONNECTIONS", raising=False)
    monkeypatch.delenv("CTEFORGE_DEFAULT_PROFILE", raising=False)
    app = create_app(web_dist=tmp_path / "none")
    assert app.state.ctx is None
    with TestClient(app, base_url=BASE_URL, client=LOCAL_PEER) as client:
        assert client.get("/api/v1/me").json()["name"] == "envuser"
        assert client.get("/api/v1/profiles").json() == []
        assert app.state.ctx is not None
        # Meaning search never loads the model inside a request (ADR 0023).
        assert app.state.ctx.background_model is True
    assert app.state.ctx is None
    assert (tmp_path / "env.db").exists()


def test_factory_refuses_auth_off_from_env_off_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CTEFORGE_AUTH", "off")
    with pytest.raises(PolicyError):
        create_app(host="10.0.0.5")


@pytest.mark.parametrize(
    ("peer", "base_url"),
    [
        (("192.168.1.20", 50000), BASE_URL),  # remote peer forging a loopback Host
        (("testclient", 50000), BASE_URL),  # not an address at all
        (LOCAL_PEER, "http://192.168.1.10:18766"),  # accepted on a LAN address
    ],
)
def test_auth_off_refuses_non_loopback_sockets_whatever_the_host_header(
    new_context, tmp_path, check_error, peer: tuple[str, int], base_url: str
) -> None:
    """A server started on 0.0.0.0 without check_bind seeing it (bare uvicorn) must not
    hand a remote client the local admin because it sends `Host: localhost`."""
    with new_context(auth=AuthMode.OFF, user="me") as ctx:
        app = create_app(ctx, web_dist=tmp_path / "none")
        with TestClient(app, base_url=base_url, client=peer) as client:
            response = client.get("/api/v1/me", headers={"Host": "localhost"})
            check_error(response, 403, "PermissionDeniedError")
            created = client.post(
                "/api/v1/users",
                headers={"Host": "localhost"},
                json={"name": "evil", "role": "admin", "kind": "human"},
            )
            check_error(created, 403, "PermissionDeniedError")
        assert ctx.store.get_user("evil") is None


def test_auth_off_accepts_ipv4_mapped_loopback_peer(new_context, tmp_path) -> None:
    with new_context(auth=AuthMode.OFF, user="me") as ctx:
        app = create_app(ctx, web_dist=tmp_path / "none")
        with TestClient(app, base_url=BASE_URL, client=("::ffff:127.0.0.1", 50000)) as client:
            assert client.get("/api/v1/me").status_code == 200
