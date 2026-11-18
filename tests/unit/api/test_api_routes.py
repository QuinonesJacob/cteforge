"""Every /api/v1 route: a happy path and at least one error path, with the contract's
status mapping (src/cteforge/api/CLAUDE.md)."""

from __future__ import annotations

import json
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
    assert api.put("/settings", "root", {"mode": "strict"}).json()["mode"] == "strict"


def _pending_revision(api, user: str = "alice", **meta: Any) -> str:
    """A pending metadata-only revision of realization_rate (strict mode blocks auto-apply)."""
    _strict(api)
    body = {"base_version": 1, "metadata": {"keywords": ["cash", *meta.pop("kw", [])]}}
    response = api.post("/proposals/definitions/realization_rate/revise", user, body | meta)
    assert response.status_code == 200, response.text
    assert response.json()["proposal"]["status"] == "pending"
    return response.json()["proposal"]["id"]


# ── identity, profiles ───────────────────────────────────────────────────────────────


def test_profiles_name_dialect_and_default_only(api, check_error) -> None:
    response = api.get("/profiles", "vic")
    assert response.status_code == 200
    assert response.json() == [
        {"name": "demo", "dialect": "duckdb", "default": True},
        {"name": "broken", "dialect": "duckdb", "default": False},
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


def test_search_document(rr, check_error) -> None:
    doc = rr.get("/definitions/realization_rate/search-document", "vic")
    assert doc.status_code == 200
    body = doc.json()
    assert (body["id"], body["version"]) == ("realization_rate", 1)
    assert body["markdown"].startswith("# Realization Rate") and "## Tables" in body["markdown"]
    same = rr.get("/definitions/realization_rate/search-document", "vic", params={"version": 1})
    assert same.json() == body
    check_error(rr.get("/definitions/nope/search-document", "vic"), 404, "NotFoundError")
    hits = rr.get("/definitions", "vic", params={"q": "realization"}).json()
    assert hits[0]["match"] == "keyword" and hits[0]["note"] is None


def test_references_and_rename(rr, check_error) -> None:
    # ADR 0024: what uses the id, then a rename proposal (auto-applied in open mode).
    refs = rr.get("/definitions/realization_rate/references", "vic")
    assert refs.status_code == 200
    body = refs.json()
    assert (body["id"], body["used_by"], body["proposals"]) == ("realization_rate", [], [])
    assert body["outside_warning"]
    check_error(rr.get("/definitions/nope/references", "vic"), 404, "NotFoundError")
    path = "/proposals/definitions/realization_rate/rename"
    check_error(rr.post(path, "vic", {"new_id": "rr_monthly"}), 403, "PermissionDeniedError")
    check_error(rr.post(path, "alice", {"new_id": "Bad Id"}), 422, "CodecError")
    check_error(
        rr.post(path, "alice", {"new_id": "x", "base_version": 9}), 409, "StaleVersionError"
    )
    renamed = rr.post(path, "alice", {"new_id": "rr_monthly", "summary": "clearer"})
    assert renamed.status_code == 200, renamed.text
    outcome = renamed.json()
    assert outcome["auto_applied"] is True and outcome["definition"]["id"] == "rr_monthly"
    assert outcome["proposal"]["kind"] == "rename_definition"
    assert outcome["proposal"]["payload"]["from_id"] == "realization_rate"
    error = check_error(rr.get("/definitions/realization_rate", "vic"), 404, "NotFoundError")
    assert "was renamed to 'rr_monthly'" in error["message"]
    card = rr.get("/definitions/rr_monthly", "vic").json()
    assert card["former_ids"] == ["realization_rate"]


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
    response = api.post("/parse", "alice", {"sql": SQL, "dialect": "duckdb"})
    assert response.status_code == 200
    report = response.json()
    assert report["schema_found"] is True
    assert {"closed_deals.L1", "deals_in_region.L1"} <= {lit["id"] for lit in report["literals"]}
    check_error(
        api.post("/parse", "alice", {"sql": "SELECT FROM WHERE (", "dialect": "duckdb"}),
        422,
        "SqlParseError",
    )
    check_error(
        api.post("/parse", "alice", {"sql": SQL, "dialect": "oracle"}),
        422,
        "RequestValidationError",
    )
    check_error(
        api.post("/parse", "alice", {"sql": SQL, "dialect": "duckdb", "profile": "nope"}),
        404,
        "NotFoundError",
    )


def test_parse_is_a_build_tool(api, check_error) -> None:
    """ADR 0027: parse_query belongs to the build tools; a viewer does not hold it."""
    check_error(
        api.post("/parse", "vic", {"sql": SQL, "dialect": "duckdb"}), 403, "PermissionDeniedError"
    )


def test_parse_without_dialect_uses_the_connections(api) -> None:
    response = api.post("/parse", "alice", {"sql": SQL})
    assert response.status_code == 200 and response.json()["dialect"] == "duckdb"
    explicit = api.post("/parse", "alice", {"sql": SQL, "dialect": "postgres"}).json()
    assert explicit["dialect"] == "postgres"
    assert "dialect_mismatch" in [i["code"] for i in explicit["issues"]]


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
    # ADR 0026: the mined edge is annotated; an agent's new edge waits for a person.
    annotate = {
        "action": "annotate",
        "left_table": "deals",
        "right_table": "accounts",
        "keys": [{"left": "account_id", "right": "account_id"}],
        "relationship": "many_to_one",
        "summary": "deals → accounts",
    }
    response = rr.post("/proposals/joins", "bot", annotate)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["proposal"]["kind"] == "new_join" and body["auto_applied"] is True
    assert body["proposal"]["payload"]["evidence"]["consistent"] is True
    by_id = {"action": "annotate", "id": "accounts__deals__account_id=account_id", "notes": "n"}
    assert rr.post("/proposals/joins", "bot", by_id).json()["auto_applied"] is True
    add = {
        "action": "add",
        "left_table": "accounts",
        "right_table": "invoices",
        "keys": [["account_id", "account_id"]],
    }
    added = rr.post("/proposals/joins", "bot", add)
    assert added.status_code == 200 and added.json()["auto_applied"] is False
    check_error(
        rr.post("/proposals/joins", "bot", add | {"join_type": "sideways"}), 422, "CodecError"
    )
    check_error(
        rr.post("/proposals/joins", "bot", add | {"action": "drop"}), 422, "RequestValidationError"
    )
    check_error(rr.post("/proposals/joins", "bot", by_id | {"id": "nope"}), 404, "NotFoundError")
    check_error(rr.post("/proposals/joins", "vic", by_id), 403, "PermissionDeniedError")
    assert rr.post("/proposals/recipes", "bot", {"recipe": {}}).status_code in (404, 405)


def test_propose_composed_definition(rr, check_error) -> None:
    body = {
        "composition": {
            "uses": {"rr": {"id": "realization_rate"}},
            "select": "SELECT rr.region, rr.realization_rate FROM rr",
            "fixed": {"rr": {"regions": ["EMEA"]}},
        },
        "dialect": "duckdb",
        "metadata": {"id": "rr_emea", "title": "RR EMEA"},
        "summary": "EMEA",
    }
    response = rr.post("/proposals/definitions", "bot", body)
    assert response.status_code == 200, response.text
    assert response.json()["auto_applied"] is True
    card = rr.get("/definitions/rr_emea", "vic").json()
    assert card["composed"] is True and card["composition"]["compiled_sql"]
    assert [p["alias"] for p in card["composition"]["parts"]] == ["rr"]
    assert "rr.period" in [v["name"] for v in card["variables"]]
    assert "rr.regions" not in card["composition"]["open_variables"]
    used = rr.get("/definitions/realization_rate", "vic").json()["used_by"]
    assert [u["definition_id"] for u in used] == ["rr_emea"]
    run = rr.post(
        "/run",
        "vic",
        {"uses": {"r": {"id": "rr_emea"}}, "values": {"r": {"rr.period": Q3_APAC["period"]}}},
    )
    assert run.status_code == 200 and run.json()["ran"] is True
    check_error(
        rr.post("/proposals/definitions", "bot", body | {"sql": "SELECT 1"}), 409, "PolicyError"
    )
    check_error(
        rr.post("/run", "vic", {"recipe_id": "x", "uses": {}}), 422, "RequestValidationError"
    )


def test_library_page(rr, check_error) -> None:
    pending = _pending_revision(rr)
    page = rr.get("/library", "ann").json()
    assert page["proposals_visible"] is True and page["pending_count"] == 1
    (row,) = page["rows"]
    assert row["id"] == "realization_rate" and [p["id"] for p in row["pending"]] == [pending]
    assert row["created_by_kind"] == "human" and row["needs_my_review"] is True
    mine = rr.get("/library", "ann", params={"needs_my_review": "true"}).json()
    assert [r["id"] for r in mine["rows"]] == ["realization_rate"]
    none = rr.get("/library", "ann", params=[("trust", "verified")]).json()
    assert none["rows"] == [] and none["total"] == 1
    agents = rr.get("/library", "ann", params=[("created_by", "agent")]).json()
    assert agents["rows"] == []
    check_error(
        rr.get("/library", "vic", params={"needs_my_review": "true"}), 403, "PermissionDeniedError"
    )
    check_error(rr.client.get("/api/v1/library"), 401, "Unauthorized")


def test_library_waiting_and_oldest_sort(rr, check_error) -> None:
    """ADR 0029: `waiting` (oldest first, +/- counts) and `sort=oldest`."""
    pending = _pending_revision(rr)
    page = rr.get("/library", "ann", params={"q": "no such words"}).json()
    assert page["rows"] == []
    (waiting,) = page["waiting"]
    assert waiting["id"] == pending and waiting["target_id"] == "realization_rate"
    assert waiting["title"] and waiting["needs_my_review"] is True
    # ADR 0029: counts are of the query diff: a keywords change is metadata only.
    assert (waiting["lines_added"], waiting["lines_removed"]) == (0, 0)
    oldest = rr.get("/library", "ann", params={"needs_my_review": "true", "sort": "oldest"}).json()
    (brief,) = oldest["rows"][0]["pending"]
    assert brief["lines_added"] == waiting["lines_added"] and brief["needs_my_review"] is True
    assert rr.get("/library", "vic").json()["waiting"] == []
    check_error(rr.get("/library", "ann", params={"sort": "newest"}), 422, "RequestValidationError")


def test_get_proposal_full_diff(rr) -> None:
    """ADR 0029: `full_diff=true` keeps every unchanged line (one hunk); default 3 lines."""
    pending = _pending_revision(rr)
    view = rr.get(f"/proposals/{pending}", "vic").json()
    assert view["title"] == "Realization Rate (monthly, by region)"  # ADR 0029
    short = view["diff"]
    full = rr.get(f"/proposals/{pending}", "vic", params={"full_diff": "true"}).json()["diff"]
    assert full.count("\n@@") == 1 and len(full.splitlines()) > len(short.splitlines())
    changed = [ln for ln in short.splitlines() if ln[:1] in "+-" and ln[:3] not in ("+++", "---")]
    assert changed == [
        ln for ln in full.splitlines() if ln[:1] in "+-" and ln[:3] not in ("+++", "---")
    ]


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
    # ADR 0029: a person's successful read through the API is not logged; a refused one is.
    rr.get("/definitions", "vic", params={"q": "realization"})
    assert rr.get("/audit", "ann", params={"actor": "vic", "kind": "search"}).json()["events"] == []
    check_error(rr.get("/audit", "vic"), 403, "PermissionDeniedError")
    response = rr.get("/audit", "ann", params={"actor": "vic", "kind": "admin", "limit": 5})
    assert response.status_code == 200
    page = response.json()
    assert page["limit"] == 5 and page["has_more"] is False
    assert [(e["kind"], e["outcome"], e["extra"]["op"]) for e in page["events"]] == [
        ("admin", "rejected", "list_audit")
    ]
    by_def = rr.get("/audit", "ann", params={"definition_id": "realization_rate"}).json()
    assert by_def["events"]
    check_error(rr.get("/audit", "vic"), 403, "PermissionDeniedError")
    check_error(rr.get("/audit", "ann", params={"kind": "bogus"}), 409, "PolicyError")


# ── admin ────────────────────────────────────────────────────────────────────────────


def test_users(api, check_error) -> None:
    response = api.get("/users", "root")
    assert response.status_code == 200
    users = response.json()
    assert {u["name"] for u in users} == {"root", "alice", "ann", "vic", "bot", "robo"}
    for u in users:
        assert "token" not in u and u["active"] is True
        assert [t["name"] for t in u["tokens"]] == ["default"]
        assert all(t["prefix"].startswith("cfg_pat_") for t in u["tokens"])
    check_error(api.get("/users", "ann"), 403, "PermissionDeniedError")


def test_user_profiles(api, check_error) -> None:
    """ADR 0029: PUT /me/profile (own), PATCH /users/{name} (manage_user), GET /me."""
    me = api.put("/me/profile", "vic", {"display_name": "Vic", "email": "vic@example.com"})
    assert me.status_code == 200, me.text
    assert (me.json()["display_name"], me.json()["email"]) == ("Vic", "vic@example.com")
    # Absent = unchanged; null or "" clears.
    assert api.put("/me/profile", "vic", {"email": None}).json()["display_name"] == "Vic"
    got = api.get("/me", "vic").json()
    assert (got["display_name"], got["email"]) == ("Vic", None)
    check_error(api.put("/me/profile", "vic", {"email": "nope"}), 409, "PolicyError")
    check_error(api.put("/me/profile", "vic", {"nickname": "x"}), 422, "RequestValidationError")
    patched = api.patch("/users/vic", "root", {"display_name": "", "email": "v@example.org"})
    assert patched.status_code == 200
    assert (patched.json()["display_name"], patched.json()["email"]) == (None, "v@example.org")
    listed = {u["name"]: u for u in api.get("/users", "root").json()}
    assert listed["vic"]["email"] == "v@example.org"
    check_error(
        api.patch("/users/root", "vic", {"display_name": "x"}), 403, "PermissionDeniedError"
    )
    check_error(api.patch("/users/vic", "root", {}), 422, "RequestValidationError")
    # The Log names actors; emails only for callers who may list users.
    audit = api.get("/audit", "root").json()
    assert audit["actors"]["vic"]["email"] == "v@example.org"
    assert api.get("/audit", "ann").json()["actors"]["vic"]["email"] is None
    # Every event recorded for a token-authenticated call names its token, by prefix.
    [event] = [
        e
        for e in audit["events"]
        if e["actor"] == "vic" and e["extra"].get("op") == "update_profile"
    ][:1]
    assert event["extra"]["token_prefix"] == api.tokens["vic"][:12]
    assert api.tokens["vic"] not in json.dumps(audit)


def _no_token_values(api, response) -> None:
    text = response.text
    for token in api.tokens.values():
        assert token not in text


def test_token_values_appear_only_in_the_mint_response(api) -> None:
    minted = api.post("/me/tokens", "alice", {"name": "Claude Code"})
    assert minted.status_code == 201 and minted.headers["cache-control"] == "no-store"
    token = minted.json()["token"]
    api.tokens["alice-cc"] = token
    assert minted.json()["info"]["prefix"] == token[:12]
    for path, user in [
        ("/users", "root"),
        ("/me/tokens", "alice"),
        ("/users/alice/tokens", "root"),
        ("/roles", "root"),
        ("/me", "alice"),
        ("/audit?limit=1000", "root"),
    ]:
        response = api.get(path, user)
        assert response.status_code == 200, path
        _no_token_values(api, response)


def test_me_includes_effective_tools(api) -> None:
    me = api.get("/me", "vic").json()
    assert me["name"] == "vic" and me["role"] == "viewer" and me["kind"] == "human"
    assert "search" in me["tools"] and "propose_definition" not in me["tools"]
    assert me["role_allowed"] == me["role_defaults"] == me["tools"]
    robo = api.get("/me", "robo").json()
    assert "verify" not in robo["tools"]  # an approver agent does not verify
    assert api.get("/tools", "vic").json()[0] == {
        "name": "search",
        "group": "use",
        "admin_only": False,
    }


def test_my_tokens(api, check_error) -> None:
    made = api.post(
        "/me/tokens",
        "alice",
        {"name": "ci", "tool_mode": "only", "include": ["search", "compile_and_run"]},
    ).json()
    narrow = made["token"]
    headers = {"Authorization": f"Bearer {narrow}"}
    me = api.client.get("/api/v1/me", headers=headers).json()
    assert me["tools"] == ["search", "compile_and_run"]
    # The narrow token can't reach other tools over HTTP either (enforced in services).
    check_error(
        api.client.post("/api/v1/parse", headers=headers, json={"sql": SQL, "dialect": "duckdb"}),
        403,
        "PermissionDeniedError",
    )
    listed = api.get("/me/tokens", "alice").json()
    assert [t["name"] for t in listed] == ["default", "ci"]
    changed = api.put(
        f"/me/tokens/{made['info']['id']}", "alice", {"tool_mode": "only", "include": ["search"]}
    )
    assert changed.json()["tools"] == ["search"]
    revoked = api.delete(f"/me/tokens/{made['info']['id']}", "alice")
    assert revoked.status_code == 200 and revoked.json()["state"] == "revoked"
    check_error(api.client.get("/api/v1/me", headers=headers), 401, "Unauthorized")
    check_error(api.delete("/me/tokens/nope", "alice"), 404, "NotFoundError")
    check_error(
        api.post("/me/tokens", "vic", {"name": "x", "include": ["propose_definition"]}),
        409,
        "PolicyError",
    )
    check_error(api.post("/me/tokens", "vic", {"name": ""}), 422, "RequestValidationError")
    check_error(
        api.post("/me/tokens", "vic", {"name": "x", "expires_at": "2000-01-01T00:00:00Z"}),
        409,
        "PolicyError",
    )


def test_agents_never_receive_tokens_over_http(api, check_error) -> None:
    check_error(api.post("/me/tokens", "bot", {"name": "x"}), 403, "PermissionDeniedError")
    api.patch("/users/robo", "root", {"role": "admin"})  # robo: an agent admin now
    check_error(api.post("/users/vic/tokens", "robo", {"name": "x"}), 403, "PermissionDeniedError")
    created = api.post("/users", "robo", {"name": "zed", "role": "viewer", "kind": "human"})
    assert created.status_code == 201 and created.json()["token"] is None
    assert created.json()["user"]["name"] == "zed"


def test_admin_mints_and_revokes_for_users(api, check_error) -> None:
    made = api.post("/users/vic/tokens", "root", {"name": "laptop"})
    assert made.status_code == 201 and made.json()["info"]["created_by"] == "root"
    token = made.json()["token"]
    me = api.client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["name"] == "vic"
    tokens = api.get("/users/vic/tokens", "root").json()
    assert {t["name"] for t in tokens} == {"default", "laptop"}
    check_error(api.get("/users/vic/tokens", "ann"), 403, "PermissionDeniedError")
    check_error(api.post("/users/vic/tokens", "ann", {"name": "x"}), 403, "PermissionDeniedError")
    check_error(api.post("/users/nobody/tokens", "root", {"name": "x"}), 404, "NotFoundError")
    revoked = api.delete(f"/users/vic/tokens/{made.json()['info']['id']}", "root")
    assert revoked.json()["state"] == "revoked"
    check_error(
        api.client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"}),
        401,
        "Unauthorized",
    )


def test_create_user_returns_token_once(api, check_error) -> None:
    response = api.post("/users", "root", {"name": "zoe", "role": "viewer", "kind": "agent"})
    assert response.status_code == 201
    body = response.json()
    assert body["user"]["name"] == "zoe" and body["user"]["kind"] == "agent"
    assert "tools" not in body["user"]
    me = api.client.get("/api/v1/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert me.json()["name"] == "zoe"
    check_error(api.post("/users", "root", {"name": "zoe", "role": "viewer"}), 409, "StoreError")
    check_error(api.post("/users", "root", {"name": "x", "role": "boss"}), 409, "PolicyError")
    check_error(
        api.post("/users", "root", {"name": "bad name!", "role": "viewer"}), 409, "PolicyError"
    )
    check_error(
        api.post("/users", "ann", {"name": "y", "role": "viewer"}), 403, "PermissionDeniedError"
    )


def test_update_user(api, check_error) -> None:
    response = api.patch("/users/vic", "root", {"role": "contributor"})
    assert response.status_code == 200 and response.json()["role"] == "contributor"
    assert api.get("/me", "vic").json()["role"] == "contributor"
    check_error(api.patch("/users/root", "root", {"role": "viewer"}), 409, "PolicyError")
    check_error(api.patch("/users/nobody", "root", {"role": "viewer"}), 404, "NotFoundError")
    check_error(api.patch("/users/vic", "root", {}), 422, "RequestValidationError")
    off = api.patch("/users/vic", "root", {"active": False})
    assert off.json()["active"] is False
    check_error(api.get("/me", "vic"), 401, "Unauthorized")
    api.patch("/users/vic", "root", {"active": True})
    assert api.get("/me", "vic").status_code == 200
    check_error(api.patch("/users/vic", "ann", {"active": False}), 403, "PermissionDeniedError")


def test_roles(api, check_error) -> None:
    roles = api.get("/roles", "root").json()
    assert [r["name"] for r in roles] == ["viewer", "contributor", "approver", "admin"]
    assert roles[3]["allowed"] == [t["name"] for t in api.get("/tools", "root").json()]
    made = api.post(
        "/roles",
        "root",
        {"name": "support_bot", "allowed": ["search", "compile_and_run"], "defaults": ["search"]},
    )
    assert made.status_code == 201 and made.json()["defaults"] == ["search"]
    edited = api.put("/roles/support_bot", "root", {"defaults": ["search", "compile_and_run"]})
    assert edited.json()["defaults"] == ["search", "compile_and_run"]
    check_error(api.put("/roles/admin", "root", {"allowed": ["search"]}), 409, "PolicyError")
    check_error(
        api.post("/roles", "root", {"name": "x", "allowed": ["manage_user"]}), 409, "PolicyError"
    )
    check_error(
        api.post("/roles", "root", {"name": "x", "allowed": ["teleport"]}),
        422,
        "RequestValidationError",
    )
    check_error(api.get("/roles", "ann"), 403, "PermissionDeniedError")
    check_error(api.delete("/roles/viewer", "root"), 409, "PolicyError")
    gone = api.delete("/roles/support_bot", "root")
    assert gone.status_code == 204
    check_error(api.delete("/roles/support_bot", "root"), 404, "NotFoundError")


def test_settings(api, check_error) -> None:
    unset = {
        "mode": "open",
        "default_connection": None,
        "effective_default_connection": "demo",  # CTEFORGE_DEFAULT_PROFILE
        "issues": [],
    }
    assert api.get("/settings", "root").json() == unset
    strict = unset | {"mode": "strict"}
    assert api.put("/settings", "root", {"mode": "strict"}).json() == strict
    assert api.get("/settings", "root").json() == strict
    check_error(api.get("/settings", "ann"), 403, "PermissionDeniedError")
    check_error(api.put("/settings", "root", {"mode": "chaos"}), 422, "RequestValidationError")
    check_error(api.put("/settings", "root", {}), 422, "RequestValidationError")
    check_error(api.put("/settings", "root", {"mode": None}), 422, "RequestValidationError")


def test_settings_default_connection(api, check_error) -> None:
    """Admin sets the default connection; /profiles and parse follow it (ADR 0021)."""
    saved = api.put("/settings", "root", {"default_connection": "broken"}).json()
    assert saved["default_connection"] == saved["effective_default_connection"] == "broken"
    assert saved["mode"] == "open"  # untouched
    defaults = {p["name"]: p["default"] for p in api.get("/profiles", "vic").json()}
    assert defaults == {"demo": False, "broken": True}
    both = api.put("/settings", "root", {"mode": "strict", "default_connection": "demo"}).json()
    assert (both["mode"], both["default_connection"]) == ("strict", "demo")
    check_error(api.put("/settings", "root", {"default_connection": "nope"}), 404, "NotFoundError")
    assert api.get("/settings", "root").json()["default_connection"] == "demo"
    check_error(
        api.put("/settings", "ann", {"default_connection": "broken"}), 403, "PermissionDeniedError"
    )
    cleared = api.put("/settings", "root", {"default_connection": None}).json()
    assert cleared["default_connection"] is None
    assert cleared["effective_default_connection"] == "demo"


def test_unknown_api_path_is_json_404(api, check_error) -> None:
    check_error(api.get("/nope", "vic"), 404, "HTTPError")


# ── discovery: join map, tables ───────────────────────────────────────────────────────


def test_joins_and_tables(rr, check_error) -> None:
    joins = rr.get("/joins", "vic", params={"table": "accounts"}).json()
    assert joins and all("accounts" in (j["left_table"], j["right_table"]) for j in joins)
    assert all(j["known"] and j["sources"] == ["realization_rate"] for j in joins)
    # ADR 0029: where each source makes the join, and the sources' state and title.
    (place,) = joins[0]["source_joins"]
    assert place == {
        "definition": "realization_rate",
        "join_type": "inner",
        "location": "cte",
        "scope": "deals_in_region",
    }
    assert joins[0]["source_states"] == {"realization_rate": "llm_annotated"}
    assert joins[0]["source_titles"]["realization_rate"].startswith("Realization")
    known = rr.get("/joins", "vic", params={"known_only": "true", "definition": "x"}).json()
    assert known == []
    tables = rr.get("/tables", "vic", params=[("name", "deals"), ("name", "nope")]).json()
    assert [(t["name"], t["found"]) for t in tables] == [("deals", True), ("nope", False)]
    assert "closed_at" in {c["name"] for c in tables[0]["columns"]}
    for path in ("/api/v1/joins", "/api/v1/tables", "/api/v1/library"):
        check_error(rr.client.get(path), 401, "Unauthorized")
    check_error(rr.get("/recipes", "vic"), 404, "HTTPError")


def test_run_with_a_raw_final_select_needs_check_and_run(rr, check_error) -> None:
    """Regression (security review, poc3): a token allowed compile_and_run but not
    check_and_run ran raw SQL through /run's final `select` — also through a composed
    definition's stored final SELECT (ADR 0026)."""
    api = rr
    made = api.post(
        "/me/tokens",
        "vic",
        {"name": "bot", "tool_mode": "only", "include": ["search", "compile_and_run"]},
    )
    headers = {"Authorization": f"Bearer {made.json()['token']}"}
    check_error(
        api.client.post("/api/v1/check", headers=headers, json={"sql": "SELECT 1"}),
        403,
        "PermissionDeniedError",
    )
    raw_select = "SELECT a.account_id, (SELECT COUNT(*) FROM rr) AS n FROM accounts AS a"
    body = {
        "uses": {"rr": {"id": "realization_rate"}},
        "values": {"rr": Q3_APAC},
        "select": raw_select,
    }
    refused = api.client.post("/api/v1/run", headers=headers, json=body)
    check_error(refused, 403, "PermissionDeniedError")
    assert "check_and_run" in refused.json()["error"]["message"]
    ok = api.client.post("/api/v1/run", headers=headers, json=body | {"select": "SELECT * FROM rr"})
    assert ok.status_code == 200 and ok.json()["ran"] is True
    # A composed definition whose stored final SELECT reads raw tables (proposed and
    # approved by people holding check_and_run) needs check_and_run to run, even verified.
    composed = {
        "composition": {"uses": {"rr": {"id": "realization_rate"}}, "select": raw_select},
        "dialect": "duckdb",
        "metadata": {"id": "raw_combo", "title": "Raw combo"},
    }
    assert api.post("/proposals/definitions", "alice", composed).json()["auto_applied"]
    assert api.post("/definitions/raw_combo/verify", "ann", {"version": 1}).status_code == 200
    run_composed = {"uses": {"c": {"id": "raw_combo"}}}
    refused = api.client.post("/api/v1/run", headers=headers, json=run_composed)
    check_error(refused, 403, "PermissionDeniedError")
    assert api.post("/run", "vic", run_composed).json()["ran"] is True  # vic holds it
    # Proposing one runs it in the checks: a proposer without check_and_run is refused.
    narrow = api.post(
        "/me/tokens",
        "alice",
        {"name": "builder", "tool_mode": "only", "include": ["propose_definition"]},
    )
    builder = {"Authorization": f"Bearer {narrow.json()['token']}"}
    again = composed | {"metadata": {"id": "raw_combo_2", "title": "Raw combo 2"}}
    check_error(
        api.client.post("/api/v1/proposals/definitions", headers=builder, json=again),
        403,
        "PermissionDeniedError",
    )
