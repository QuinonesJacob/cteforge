"""The whole loop over HTTP: propose → search → run → approve+verify → audit (DESIGN §18),
and that the nav-badge count is not audited."""

from __future__ import annotations

from cteforge.domain.audit import AuditKind


def _ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def test_propose_search_run_approve_verify_audit(api) -> None:
    # Strict mode: nothing is usable until a person verifies it; proposals wait.
    _ok(api.put("/settings", "root", {"mode": "strict"}))

    # 1. An agent proposes the trusted query; it waits for review.
    outcome = _ok(api.propose_rr(user="bot"))
    proposal_id = outcome["proposal"]["id"]
    assert outcome["auto_applied"] is False and outcome["proposal"]["status"] == "pending"
    assert _ok(api.get("/proposals/count", "vic", params={"status": "pending"})) == {"count": 1}

    # 2. Search: a pending proposal is not in the library yet.
    assert _ok(api.get("/definitions", "vic", params={"q": "realization"})) == []

    # 3. Run: the definition does not exist until approved.
    body = {"uses": {"rr": {"id": "realization_rate"}}, "run": True, "limit": 100}
    assert api.post("/run", "bot", body).status_code == 404

    # 4. A human approver approves and verifies (four-eyes: bot authored it).
    decided = _ok(
        api.post(f"/proposals/{proposal_id}/approve", "ann", {"verify": True, "comment": "lgtm"})
    )
    assert decided["proposal"]["status"] == "approved"
    assert decided["definition"]["state"] == "verified"
    assert decided["definition"]["verified_by"] == "ann"
    assert _ok(api.get("/proposals/count", "vic", params={"status": "pending"})) == {"count": 0}

    hits = _ok(api.get("/definitions", "vic", params={"q": "realization"}))
    assert [(h["id"], h["state"]) for h in hits] == [("realization_rate", "verified")]

    run = _ok(api.post("/run", "bot", body | {"question": "How much did we collect?"}))
    assert run["ran"] and run["label"] == "verified" and run["row_count"] > 0
    assert run["used"] == [
        {
            "id": "realization_rate",
            "version": 1,
            "state": "verified",
            "verified_by": "ann",
            "verified_at": decided["definition"]["verified_at"],
        }
    ]

    # 5. Audit: every step is attributed to its actor.
    events = _ok(api.get("/audit", "ann", params={"limit": 1000}))["events"]
    seen = {(e["actor"], e["kind"]) for e in events}
    assert {
        ("bot", "propose"),
        ("vic", "search"),
        ("bot", "compile_and_run"),
        ("ann", "review"),
        ("root", "admin"),
    } <= seen
    runs = [e for e in events if e["kind"] == "compile_and_run" and e["outcome"] == "ok"]
    assert runs[0]["question"] == "How much did we collect?"
    assert runs[0]["trust_label"] == "verified" and runs[0]["definitions"] == ["realization_rate@1"]
    assert runs[0]["compile_id"] in run["sql"]
    assert not any("rows" in (e.get("extra") or {}) for e in events)


def test_proposal_count_is_not_audited(api) -> None:
    api.propose_rr()
    before = api.ctx.audit.list_events(limit=1000)
    for status in ("pending", "approved", None):
        params = {"status": status} if status else {}
        assert api.get("/proposals/count", "vic", params=params).status_code == 200
    after = api.ctx.audit.list_events(limit=1000)
    assert [e.id for e in after] == [e.id for e in before]
    # ...whereas listing proposals is audited.
    api.get("/proposals", "vic")
    listed = api.ctx.audit.list_events(limit=1000, kind=AuditKind.REVIEW, actor="vic")
    assert len(listed) == 1
