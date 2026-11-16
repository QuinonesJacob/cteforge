"""Every /api/v1 route: a happy path and at least one error path, with the contract's
status mapping (src/cteforge/api/CLAUDE.md)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"
SQL = (EXAMPLES / "queries" / "realization_rate.sql").read_text()
Q3_APAC = {"period": {"start": "2025-07-01", "end": "2025-10-01"}, "regions": ["APAC"]}


@pytest.fixture
def rr(api):
    """realization_rate v1, proposed by alice and auto-applied (open mode)."""
    response = api.propose_rr()
    assert response.status_code == 200, response.text
    assert response.json()["auto_applied"] is True
    return api


def _strict(api) -> None:
    assert api.put("/settings", "root", {"mode": "strict"}).json() == {"mode": "strict"}


def _pending_revision(api, user: str = "alice", **meta: Any) -> str:
    """A pending metadata-only revision of realization_rate (strict mode blocks auto-apply)."""
    _strict(api)
    body = {"base_version": 1, "metadata": {"keywords": ["cash", *meta.pop("kw", [])]}}
    response = api.post("/proposals/definitions/realization_rate/revise", user, body | meta)
    assert response.status_code == 200, response.text
    assert response.json()["proposal"]["status"] == "pending"
    return response.json()["proposal"]["id"]


# ── identity, profiles ───────────────────────────────────────────────────────────────


def test_profiles_name_and_dialect_only(api, check_error) -> None:
    response = api.get("/profiles", "vic")
    assert response.status_code == 200
    assert response.json() == [
        {"name": "demo", "dialect": "duckdb"},
        {"name": "broken", "dialect": "duckdb"},
    ]
    check_error(api.client.get("/api/v1/profiles"), 401, "Unauthorized")


# ── definitions ──────────────────────────────────────────────────────────────────────


def test_search(rr, check_error) -> None:
    response = rr.get("/definitions", "vic", params={"q": "realization", "limit": 100})
    assert response.status_code == 200
    hits = response.json()
    assert [h["id"] for h in hits] == ["realization_rate"]
    assert hits[0]["state"] == "llm_annotated" and hits[0]["variables"]
    assert rr.get("/definitions", "vic", params={"q": "zebra"}).json() == []
    check_error(
        rr.get("/definitions", "vic", params={"limit": "many"}), 422, "RequestValidationError"
    )


def test_get_definition_and_history(rr, check_error) -> None:
    card = rr.get("/definitions/realization_rate", "vic").json()
    assert card["id"] == "realization_rate" and card["version"] == 1
    assert [h["version"] for h in card["history"]] == [1]
    assert rr.get("/definitions/realization_rate", "vic", params={"version": 1}).status_code == 200
    history = rr.get("/definitions/realization_rate/history", "vic")
    assert history.status_code == 200
    assert history.json()[0]["author"] == "alice"
    check_error(rr.get("/definitions/nope", "vic"), 404, "NotFoundError")
    check_error(
        rr.get("/definitions/realization_rate", "vic", params={"version": 9}),
        404,
        "NotFoundError",
    )
    check_error(rr.get("/definitions/nope/history", "vic"), 404, "NotFoundError")


def test_verify_definition(rr, check_error) -> None:
    check_error(
        rr.post("/definitions/realization_rate/verify", "vic", {"version": 1}),
        403,
        "PermissionDeniedError",
    )
    # Agents never verify, whatever their role (ADR 0007).
    check_error(
        rr.post("/definitions/realization_rate/verify", "robo", {"version": 1}),
        403,
        "PermissionDeniedError",
    )
    stale = check_error(
        rr.post("/definitions/realization_rate/verify", "ann", {"version": 2}),
        409,
        "StaleVersionError",
    )
    assert [i["code"] for i in stale["issues"]] == ["stale_base_version"]
    response = rr.post("/definitions/realization_rate/verify", "ann", {"version": 1})
    assert response.status_code == 200
    assert response.json()["state"] == "verified" and response.json()["verified_by"] == "ann"


def test_parse(api, check_error) -> None:
    response = api.post("/parse", "vic", {"sql": SQL, "dialect": "duckdb"})
    assert response.status_code == 200
    report = response.json()
    assert report["schema_found"] is True
    assert {"closed_deals.L1", "deals_in_region.L1"} <= {lit["id"] for lit in report["literals"]}
    check_error(
        api.post("/parse", "vic", {"sql": "SELECT FROM WHERE (", "dialect": "duckdb"}),
        422,
        "SqlParseError",
    )
    check_error(
        api.post("/parse", "vic", {"sql": SQL, "dialect": "oracle"}),
        422,
        "RequestValidationError",
    )
    check_error(
        api.post("/parse", "vic", {"sql": SQL, "dialect": "duckdb", "profile": "nope"}),
        404,
        "NotFoundError",
    )


# ── run / check / revalidate ─────────────────────────────────────────────────────────


def test_run(rr, check_error) -> None:
    body = {
        "uses": {"rr": {"id": "realization_rate"}},
        "values": {"rr": Q3_APAC},
        "run": True,
        "limit": 50,
        "profile": "demo",
        "question": "Q3 APAC?",
    }
    response = rr.post("/run", "bot", body)
    assert response.status_code == 200, response.text
    out = response.json()
    assert out["ran"] is True and out["usable"] is True
    assert out["label"] == "llm_annotated"
    assert out["columns"] == ["month", "region", "realization_rate"]
    assert {row[1] for row in out["rows"]} == {"APAC"}
    assert out["definitions"] == ["realization_rate@1"]
    bad = check_error(
        rr.post("/run", "bot", body | {"values": {"rr": {"period": "garbage"}}}),
        422,
        "VariableValueError",
    )
    assert "period" in bad["message"]
    check_error(rr.post("/run", "bot", {"uses": {"x": {"id": "nope"}}}), 404, "NotFoundError")
    check_error(rr.post("/run", "bot", body | {"extra": 1}), 422, "RequestValidationError")


def test_run_without_executing(rr) -> None:
    out = rr.post("/run", "vic", {"uses": {"rr": {"id": "realization_rate"}}, "run": False})
    assert out.status_code == 200
    assert out.json()["ran"] is False and "realization_rate v1" in out.json()["sql"]


def test_check(api, check_error) -> None:
    response = api.post("/check", "vic", {"sql": "SELECT region FROM accounts", "limit": 5})
    assert response.status_code == 200
    out = response.json()
    assert out["ran"] is True and out["label"] in ("new_logic_on_known_joins", "exploratory")
    assert out["columns"] == ["region"]
    refused = api.post("/check", "vic", {"sql": "DELETE FROM accounts"})
    assert refused.status_code == 200 and refused.json()["usable"] is False
    check_error(api.post("/check", "vic", {"sql": "SELECT FROM WHERE ("}), 422, "SqlParseError")


def test_revalidate(rr, check_error) -> None:
    response = rr.post("/revalidate", "vic", {"ids": ["realization_rate"]})
    assert response.status_code == 200
    [drift] = response.json()
    assert drift["id"] == "realization_rate" and drift["ok"] is True
    whole = rr.post("/revalidate", "ann", {})
    assert whole.status_code == 200 and [d["id"] for d in whole.json()] == ["realization_rate"]
    check_error(rr.post("/revalidate", "vic", {}), 403, "PermissionDeniedError")
    check_error(rr.post("/revalidate", "vic", {"ids": ["nope"]}), 404, "NotFoundError")


# ── proposals ────────────────────────────────────────────────────────────────────────


def test_propose_definition(api, check_error) -> None:
    response = api.propose_rr()
    assert response.status_code == 200
    outcome = response.json()
    assert outcome["proposal"]["author"] == "alice"
    assert outcome["definition"]["id"] == "realization_rate"
    dup = check_error(api.propose_rr(), 409, "StaleVersionError")
    assert dup["issues"][0]["code"] == "stale_base_version"
    check_error(api.propose_rr(user="vic", id="other"), 403, "PermissionDeniedError")
    check_error(api.propose_rr(id=None), 422, "CodecError")
    check_error(
        api.post("/proposals/definitions", "alice", api.proposal_body() | {"surprise": True}),
        422,
        "RequestValidationError",
    )


def test_propose_definition_warehouse_failure_is_502(api, check_error) -> None:
    body = api.proposal_body(id="rr_broken") | {"profile": "broken"}
    error = check_error(api.post("/proposals/definitions", "alice", body), 502, "WarehouseError")
    assert "not a database" not in error["message"]


def test_revise_definition(rr, check_error) -> None:
    response = rr.post(
        "/proposals/definitions/realization_rate/revise",
        "alice",
        {"base_version": 1, "metadata": {"keywords": ["cash"]}, "summary": "kw"},
    )
    assert response.status_code == 200
    assert response.json()["proposal"]["kind"] == "revise_definition"
    assert response.json()["proposal"]["resulting_version"] == 2
    stale = check_error(
        rr.post("/proposals/definitions/realization_rate/revise", "alice", {"base_version": 1}),
        409,
        "StaleVersionError",
    )
    assert stale["issues"][0]["code"] == "stale_base_version"


def test_propose_join(rr, check_error) -> None:
    body = {
        "left_table": "deals",
        "right_table": "accounts",
        "keys": [{"left": "account_id", "right": "account_id"}],
        "join_type": "inner",
        "relationship": "many_to_one",
        "summary": "deals → accounts",
    }
    response = rr.post("/proposals/joins", "bot", body)
    assert response.status_code == 200, response.text
    assert response.json()["proposal"]["kind"] == "new_join"
    pair = rr.post("/proposals/joins", "bot", body | {"keys": [["account_id", "account_id"]]})
    assert pair.status_code == 200
    check_error(
        rr.post("/proposals/joins", "bot", body | {"join_type": "sideways"}), 422, "CodecError"
    )


def test_propose_recipe(rr, check_error) -> None:
    recipe = {
        "id": "rr_emea",
        "title": "RR EMEA",
        "uses": {"rr": {"id": "realization_rate"}},
        "values": {"rr": {"regions": ["EMEA"]}},
        "dialect": "duckdb",
    }
    response = rr.post("/proposals/recipes", "bot", {"recipe": recipe, "summary": "EMEA"})
    assert response.status_code == 200, response.text
    assert response.json()["proposal"]["kind"] == "new_recipe"
    assert response.json()["issues"] == []
    check_error(rr.post("/proposals/recipes", "bot", {"recipe": {"id": "x"}}), 422, "CodecError")


def test_list_get_and_count_proposals(rr, check_error) -> None:
    pending = _pending_revision(rr)
    listed = rr.get("/proposals", "vic", params={"status": "pending"})
    assert listed.status_code == 200
    assert [p["id"] for p in listed.json()] == [pending]
    assert listed.json()[0]["diff"]
    assert len(rr.get("/proposals", "vic").json()) == 2
    count = rr.get("/proposals/count", "vic", params={"status": "pending"})
    assert count.status_code == 200 and count.json() == {"count": 1}
    assert rr.get("/proposals/count", "vic").json() == {"count": 2}
    got = rr.get(f"/proposals/{pending}", "vic")
    assert got.status_code == 200 and got.json()["status"] == "pending"
    check_error(rr.get("/proposals/nope", "vic"), 404, "NotFoundError")
    check_error(
        rr.get("/proposals", "vic", params={"status": "maybe"}), 422, "RequestValidationError"
    )
    check_error(
        rr.get("/proposals/count", "vic", params={"status": "maybe"}), 422, "RequestValidationError"
    )
    check_error(rr.client.get("/api/v1/proposals/count"), 401, "Unauthorized")


def test_approve(rr, check_error) -> None:
    pid = _pending_revision(rr)
    check_error(rr.post(f"/proposals/{pid}/approve", "vic", {}), 403, "PermissionDeniedError")
    check_error(
        rr.post(f"/proposals/{pid}/approve", "robo", {"verify": True}), 403, "PermissionDeniedError"
    )
    response = rr.post(f"/proposals/{pid}/approve", "ann", {"verify": False, "comment": "ok"})
    assert response.status_code == 200
    assert response.json()["proposal"]["status"] == "approved"
    assert response.json()["definition"]["version"] == 2
    again = check_error(rr.post(f"/proposals/{pid}/approve", "ann", {}), 409, "PolicyError")
    assert "approved" in again["message"]
    check_error(rr.post("/proposals/nope/approve", "ann", {}), 404, "NotFoundError")


def test_four_eyes_author_may_not_approve_own(rr, check_error) -> None:
    pid = _pending_revision(rr, user="ann")  # an approver authors the change
    check_error(rr.post(f"/proposals/{pid}/approve", "ann", {}), 403, "PermissionDeniedError")


def test_reject(rr, check_error) -> None:
    pid = _pending_revision(rr)
    check_error(rr.post(f"/proposals/{pid}/reject", "ann", {"comment": "  "}), 409, "PolicyError")
    check_error(rr.post(f"/proposals/{pid}/reject", "ann", {}), 422, "RequestValidationError")
    response = rr.post(f"/proposals/{pid}/reject", "ann", {"comment": "no"})
    assert response.status_code == 200
    assert response.json()["status"] == "rejected" and response.json()["comment"] == "no"


def test_withdraw(rr, check_error) -> None:
    pid = _pending_revision(rr)
    check_error(rr.post(f"/proposals/{pid}/withdraw", "bot"), 403, "PermissionDeniedError")
    response = rr.post(f"/proposals/{pid}/withdraw", "alice")
    assert response.status_code == 200 and response.json()["status"] == "withdrawn"
    check_error(rr.post(f"/proposals/{pid}/withdraw", "alice"), 409, "PolicyError")


# ── audit ────────────────────────────────────────────────────────────────────────────


def test_audit(rr, check_error) -> None:
    rr.get("/definitions", "vic", params={"q": "realization"})
    response = rr.get("/audit", "ann", params={"actor": "vic", "kind": "search", "limit": 5})
    assert response.status_code == 200
    page = response.json()
    assert page["limit"] == 5 and page["has_more"] is False
    assert [e["kind"] for e in page["events"]] == ["search"]
    assert page["events"][0]["actor"] == "vic"
    by_def = rr.get("/audit", "ann", params={"definition_id": "realization_rate"}).json()
    assert by_def["events"]
    check_error(rr.get("/audit", "vic"), 403, "PermissionDeniedError")
    check_error(rr.get("/audit", "ann", params={"kind": "bogus"}), 409, "PolicyError")


# ── admin ────────────────────────────────────────────────────────────────────────────


def test_users(api, check_error) -> None:
    response = api.get("/users", "root")
    assert response.status_code == 200
    assert {u["name"] for u in response.json()} == {"root", "alice", "ann", "vic", "bot", "robo"}
    assert all("token" not in u for u in response.json())
    check_error(api.get("/users", "ann"), 403, "PermissionDeniedError")


def test_create_user_returns_token_once(api, check_error) -> None:
    response = api.post("/users", "root", {"name": "zoe", "role": "viewer", "kind": "agent"})
    assert response.status_code == 201
    body = response.json()
    assert body["user"]["name"] == "zoe" and body["user"]["kind"] == "agent"
    me = api.client.get("/api/v1/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert me.json()["name"] == "zoe"
    check_error(api.post("/users", "root", {"name": "zoe", "role": "viewer"}), 409, "StoreError")
    check_error(
        api.post("/users", "root", {"name": "x", "role": "boss"}), 422, "RequestValidationError"
    )
    check_error(
        api.post("/users", "root", {"name": "bad name!", "role": "viewer"}), 409, "PolicyError"
    )
    check_error(
        api.post("/users", "ann", {"name": "y", "role": "viewer"}), 403, "PermissionDeniedError"
    )


def test_rotate_token(api, check_error) -> None:
    response = api.post("/users/alice/token", "root")
    assert response.status_code == 200 and response.json()["name"] == "alice"
    check_error(api.post("/users/nobody/token", "root"), 404, "NotFoundError")
    check_error(api.post("/users/root/token", "vic"), 403, "PermissionDeniedError")


def test_set_role(api, check_error) -> None:
    response = api.put("/users/vic/role", "root", {"role": "contributor"})
    assert response.status_code == 200 and response.json()["role"] == "contributor"
    assert api.get("/me", "vic").json()["role"] == "contributor"
    check_error(api.put("/users/root/role", "root", {"role": "viewer"}), 409, "PolicyError")
    check_error(api.put("/users/nobody/role", "root", {"role": "viewer"}), 404, "NotFoundError")


def test_settings(api, check_error) -> None:
    assert api.get("/settings", "root").json() == {"mode": "open"}
    assert api.put("/settings", "root", {"mode": "strict"}).json() == {"mode": "strict"}
    assert api.get("/settings", "root").json() == {"mode": "strict"}
    check_error(api.get("/settings", "ann"), 403, "PermissionDeniedError")
    check_error(api.put("/settings", "root", {"mode": "chaos"}), 422, "RequestValidationError")


def test_unknown_api_path_is_json_404(api, check_error) -> None:
    check_error(api.get("/nope", "vic"), 404, "HTTPError")


# ── discovery: recipes, join map, tables ─────────────────────────────────────────────


def test_recipes_joins_and_tables(rr, check_error) -> None:
    recipe = {
        "recipe": {
            "id": "apac_q3_rr",
            "title": "APAC realization",
            "uses": {"rr": {"id": "realization_rate"}},
            "values": {"rr": {"regions": ["APAC"]}},
            "dialect": "duckdb",
        }
    }
    assert rr.post("/proposals/recipes", "alice", recipe).status_code == 200
    recipes = rr.get("/recipes", "vic", params={"q": "apac"}).json()
    assert [r["id"] for r in recipes] == ["apac_q3_rr"]
    joins = rr.get("/joins", "vic", params={"table": "accounts"}).json()
    assert joins and all("accounts" in (j["left_table"], j["right_table"]) for j in joins)
    tables = rr.get("/tables", "vic", params=[("name", "deals"), ("name", "nope")]).json()
    assert [(t["name"], t["found"]) for t in tables] == [("deals", True), ("nope", False)]
    assert "closed_at" in {c["name"] for c in tables[0]["columns"]}
    check_error(rr.client.get("/api/v1/joins"), 401, "Unauthorized")
