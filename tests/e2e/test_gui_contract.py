"""GUI ↔ API contract: every call in web/src/api/endpoints.ts, with the body shapes the GUI
sends (lib/importBuilder.ts, lib/library.ts, lib/joins.ts, lib/profile.ts, pages/*),
against the real FastAPI app.
Each response is checked for the fields web/src/api/types.ts declares and the GUI reads.

The GUI does not run queries (ADR 0029): `POST /run` and `POST /check` stay for agents,
scheduled jobs and the CLI and are covered by the API tests (tests/unit/api), not here.

If this fails, either the API or the GUI changed a field: fix both together.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from cteforge.api.app import create_app
from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.dialects import Dialect
from cteforge.domain.users import ActorKind, UserRole
from cteforge.services import admin
from cteforge.services.context import AppConfig, open_context

# ── Field sets from web/src/api/types.ts (required + optional fields the API always sends) ──
USER = {"name", "role", "kind", "created_at"}
# ADR 0027: GET /me (MeCard), users, roles, tokens, the tool catalog.
ME = USER | {
    "active",
    "tools",
    "review_all",
    "role_allowed",
    "role_defaults",
    "token_id",
    "token_expires_at",
    "token_mode",
    # ADR 0029: the user's own display name and email (user menu, My tokens).
    "display_name",
    "email",
}
TOOL_INFO = {"name", "group", "admin_only"}
TOKEN_CARD = {
    "id",
    "user_name",
    "name",
    "prefix",
    "created_at",
    "created_by",
    "last_used_at",
    "expires_at",
    "revoked_at",
    "tool_mode",
    "include",
    "exclude",
    "tools",
    "state",
    "parent_token_id",
}
USER_VIEW = USER | {"active", "tokens", "display_name", "email"}
ROLE_CARD = {"name", "description", "allowed", "defaults", "review_all", "builtin", "users"}
TEAM_SETTINGS = {"mode", "default_connection", "effective_default_connection", "issues"}
ISSUE = {"code", "severity", "message", "variable", "literal"}
VARIABLE_CARD = {
    "name",
    "type",
    "role",
    "input_format",
    "description",
    "allowed",
    "unit",
    "format",
    "column",
    "column_type",
    "shape",
    "original_value",
    "places",
}
SEARCH_HIT = {
    "id",
    "version",
    "title",
    "meaning",
    "state",
    "score",
    "match",
    "note",
    "keywords",
    "variables",
}
SEARCH_MATCHES = {"keyword", "meaning", "both"}
VERSION_INFO = {
    "id",
    "version",
    "state",
    "author",
    "created_at",
    "change_note",
    "logic_changed",
    "verified_by",
    "verified_at",
}
DEFINITION_CARD = {
    "id",
    "version",
    "title",
    "meaning",
    "keywords",
    "use_when",
    "dont_use_when",
    "source_dialect",
    "profile",
    "state",
    "verified_by",
    "verified_by_kind",
    "verified_at",
    "owner",
    "grain",
    "join_keys",
    "outputs",
    "tables",
    "variables",
    "template",
    "history",
    # ADR 0026: composed definitions, "used by", "joins to".
    "composed",
    "composition",
    "used_by",
    "joins",
}
# Renames (ADR 0024): GET /definitions/{id}/references, DefinitionCard.former_ids.
REFERENCES = {"id", "version", "state", "used_by", "proposals", "former_ids", "outside_warning"}
USED_BY_REF = {"definition_id", "definition_version", "title", "state", "alias", "pinned_version"}
# ADR 0026: CompositionCard / PartCard on composed definitions and their proposals.
COMPOSITION_CARD = {
    "parts",
    "fixed",
    "open_variables",
    "select",
    "compiled_sql",
    "dialect",
    "issues",
}
PART_CARD = {
    "alias",
    "id",
    "pinned_version",
    "version",
    "title",
    "state",
    "latest_version",
    "superseded",
    "usable",
    "composed",
}
# GET /library (LibraryPage / LibraryRow / ProposalBrief).
LIBRARY_PAGE = {"rows", "total", "pending_count", "proposals_visible", "waiting"}
LIBRARY_ROW = {
    "id",
    "title",
    "meaning",
    "version",
    "state",
    "composed",
    "owner",
    "created_by",
    "created_by_kind",
    "verified_by",
    "verified_by_kind",
    "approval",
    "latest_proposal",
    "pending",
    "needs_my_review",
}
PROPOSAL_BRIEF = {
    "id",
    "kind",
    "status",
    "author",
    "author_kind",
    "created_at",
    "summary",
    # ADR 0029: changed lines (pending proposals) and whether the caller may decide it.
    "lines_added",
    "lines_removed",
    "needs_my_review",
}
# ADR 0029: the Library's "Waiting on you" panel.
WAITING = PROPOSAL_BRIEF | {"target_id", "title"}
PROPOSAL_REF = {
    "proposal_id",
    "kind",
    "target_id",
    "summary",
    "author",
    "created_at",
    "relation",
}
FORMER_ID = {"former_id", "definition_id", "renamed_to", "renamed_by", "renamed_at"}
# Only with ?include_source=true (the revise flow): DefinitionCard.source_sql / annotations.
SOURCE = {"source_sql", "annotations"}
ANNOTATION = {"name", "type", "role", "literals", "column", "description", "allowed", "format"}
JOIN_CARD = {
    "id",
    "left_table",
    "right_table",
    "keys",
    "join_type",
    "relationship",
    "usage_count",
    "traps",
    "notes",
    "sources",
    "trusted_sources",
    "added_by",
    "added_by_kind",
    "confirmed_by",
    "evidence",
    "known",
    "confirmed",
    "pending_proposals",
    # ADR 0029: per-source join type and location; the sources' states and titles.
    "source_joins",
    "source_states",
    "source_titles",
}
JOIN_SOURCE = {"definition", "join_type", "location", "scope"}
EVIDENCE = {"claimed", "observed", "left", "right", "consistent", "profile", "checked_at"}
SIDE_COUNT = {"table", "keys", "rows", "distinct_keys"}
TABLE_CARD = {"name", "found", "columns", "used_by", "joins", "units"}
LITERAL = {
    "id",
    "scope",
    "value",
    "is_string",
    "form",
    "cast_type",
    "operator",
    "column_sql",
    "column",
    "wrapped",
    "in_list",
    "context",
    "wrapper_sql",
    "suggested_format",
    "compared_type",
}
COLUMN_REF = {"table", "column", "type", "derived"}
RANGE = {"scope", "column_sql", "start", "end", "shape"}
JOIN = {"scope", "left_table", "right_table", "join_type", "keys", "on_sql"}
CTE = {"name", "reads", "outputs"}
GRAIN = {"id", "scope", "unit", "column_sql", "sql", "column", "column_type", "output", "context"}
PARSE_REPORT = {
    "dialect",
    "tables",
    "outputs",
    "ctes",
    "literals",
    "ranges",
    "joins",
    "grains",
    "schema_found",
    "issues",
}
PROPOSAL_VIEW = {
    "id",
    "kind",
    "target_id",
    "title",
    "base_version",
    "payload",
    "checks",
    "summary",
    "author",
    "author_kind",
    "created_at",
    "status",
    "auto_applied",
    "decided_by",
    "decided_by_kind",
    "decided_at",
    "comment",
    "resulting_version",
    "diff",
    "impact",
    "composition",
    # ADR 0029: the query's diff and the described fields that change (ProposalPage).
    "sql_diff",
    "metadata_changes",
}
METADATA_CHANGE = {"field", "before", "after"}
PROPOSAL_OUTCOME = {"proposal", "definition", "issues", "auto_applied"}
AUDIT_EVENT = {
    "id",
    "at",
    "actor",
    "actor_kind",
    "kind",
    "question",
    "definitions",
    "values",
    "trust_label",
    "compile_id",
    "sql_sha256",
    "row_count",
    "duration_ms",
    "outcome",
    "error",
    "extra",
}
STATES = {"draft", "llm_annotated", "verified"}
LABELS = {
    "verified",
    "built_from_verified_parts",
    "llm_annotated",
    "new_logic_on_known_joins",
    "exploratory",
}

AUDIT_PAGE = {"events", "limit", "has_more", "actors"}
AUDIT_ACTOR = {"display_name", "email", "kind"}

BASE_URL = "http://127.0.0.1:8765"


def fields(obj: Any, expected: set[str], what: str) -> dict[str, Any]:
    assert isinstance(obj, dict), f"{what}: expected an object, got {type(obj).__name__}"
    missing = expected - set(obj)
    assert not missing, f"{what} lacks fields the GUI reads: {sorted(missing)}"
    return obj


def each(items: Any, expected: set[str], what: str) -> list[dict[str, Any]]:
    assert isinstance(items, list), f"{what}: expected a list"
    return [fields(item, expected, what) for item in items]


def gui_annotation(var: dict[str, Any]) -> dict[str, Any]:
    """lib/importBuilder.ts toAnnotation: only the keys the GUI ever sends."""
    ann = {k: var[k] for k in ("name", "type", "role", "literals")}
    ann["description"] = str(var.get("description", "")).strip()
    for key in ("column", "allowed", "format", "unit"):
        if var.get(key):
            ann[key] = var[key]
    return ann


def gui_proposal(sql: str, ann: dict[str, Any], owner: str, profile: str) -> dict[str, Any]:
    """pages/ImportPage.tsx → POST /proposals/definitions (lib/importBuilder.ts toMetadata)."""
    return {
        "sql": sql,
        "dialect": "duckdb",
        "metadata": {
            "id": ann["id"],
            "title": ann["title"],
            "meaning": ann["meaning"],
            "keywords": list(ann["keywords"]),
            "use_when": ann["use_when"],
            "dont_use_when": ann["dont_use_when"],
            "grain": ann.get("grain") or None,
            "join_keys": [],
            "owner": owner or None,
        },
        "annotations": [gui_annotation(v) for v in ann["variables"]],
        "profile": profile,
        "summary": f"Import {ann['title']}",
    }


def _library(gui: Gui, user: str, **lists: list[str]) -> dict[str, Any]:
    """endpoints.ts listLibrary: list filters as repeated query params."""
    params = [(k, v) for k, values in lists.items() for v in values]
    response = gui.client.get(
        "/api/v1/library",
        params=params,
        headers={"Authorization": f"Bearer {gui.tokens[user]}"},
    )
    assert response.status_code == 200, response.text
    return fields(response.json(), LIBRARY_PAGE, "LibraryPage")


def composed_definition(gui: Gui, user: str = "alice", id: str = "rr_apac") -> dict[str, Any]:
    """A composed definition as an agent proposes it (MCP propose_definition); the GUI does
    not create them, it shows them."""
    return gui.call(
        "POST",
        "/proposals/definitions",
        user,
        body={
            "composition": {
                "uses": {"rr": {"id": "realization_rate"}},
                "select": (
                    "SELECT region, AVG(realization_rate) AS avg_rate FROM rr GROUP BY region"
                ),
                "fixed": {"rr": {"regions": ["APAC"]}},
            },
            "dialect": "duckdb",
            "metadata": {"id": id, "title": "RR APAC by region"},
            "summary": "composed",
        },
    )


class Gui:
    """Calls the API exactly as web/src/api/client.ts does (bearer token, JSON, query
    params with empty values dropped)."""

    def __init__(self, client: TestClient, tokens: dict[str, str]) -> None:
        self.client = client
        self.tokens = tokens

    def call(
        self,
        method: str,
        path: str,
        user: str | None,
        *,
        body: Any = None,
        query: dict[str, Any] | None = None,
        status: int = 200,
    ) -> Any:
        params = {
            k: (str(v).lower() if isinstance(v, bool) else str(v))
            for k, v in (query or {}).items()
            if v not in (None, "")
        }
        headers = {"Accept": "application/json"}
        if user is not None:
            headers["Authorization"] = f"Bearer {self.tokens[user]}"
        kwargs: dict[str, Any] = {"headers": headers, "params": params}
        if body is not None:
            kwargs["json"] = body
        response: httpx.Response = self.client.request(method, f"/api/v1{path}", **kwargs)
        assert response.status_code == status, f"{method} {path}: {response.text}"
        return response.json() if response.content else None


@pytest.fixture
def gui(tmp_path: Path, warehouse_path: Path) -> Iterator[Gui]:
    shutil.copy(warehouse_path, tmp_path / "warehouse.duckdb")
    profiles = [
        WarehouseProfile(
            name="demo",
            dialect=Dialect.DUCKDB,
            options={"path": str(tmp_path / "warehouse.duckdb")},
        )
    ]
    config = AppConfig(db_url=f"sqlite:///{tmp_path / 'lib.db'}", default_profile="demo")
    with open_context(config, profiles=profiles) as ctx:
        root, root_token = admin.bootstrap_admin(ctx, "root")
        tokens = {"root": root_token}
        for name, role in (("alice", UserRole.CONTRIBUTOR), ("ann", UserRole.APPROVER)):
            _, tokens[name] = admin.create_user(ctx, root, name, role, ActorKind.HUMAN)
        app = create_app(ctx, web_dist=tmp_path / "no-dist")
        with TestClient(app, base_url=BASE_URL, raise_server_exceptions=False) as client:
            yield Gui(client, tokens)


def test_identity_profiles_and_sign_in(gui: Gui) -> None:
    # AuthContext: an anonymous /me tells "auth on" (401 + error body) from "auth off".
    error = gui.call("GET", "/me", None, status=401)
    fields(error["error"], {"type", "message", "issues"}, "ApiError body")
    me = fields(gui.call("GET", "/me", "alice"), ME, "User (MeCard)")
    assert (me["name"], me["role"], me["kind"]) == ("alice", "contributor", "human")
    # lib/roles.ts reads `tools` (nav links, Import, Review, verify offers) and review_all.
    assert {"parse_query", "propose_definition", "review_proposal"} <= set(me["tools"])
    assert "verify" not in me["tools"] and me["review_all"] is False
    profiles = each(
        gui.call("GET", "/profiles", "alice"), {"name", "dialect", "default"}, "Profile"
    )
    assert profiles == [{"name": "demo", "dialect": "duckdb", "default": True}]
    assert me["display_name"] is None and me["email"] is None

    # ADR 0029: Settings → My tokens → Your profile: PUT /me/profile (lib/profile.ts sends only
    # the changed fields; "" clears one) → the MeCard, which GET /me then returns.
    updated = fields(
        gui.call(
            "PUT",
            "/me/profile",
            "alice",
            body={"display_name": "Alice Ng", "email": "alice@example.com"},
        ),
        ME,
        "User (MeCard)",
    )
    assert (updated["display_name"], updated["email"]) == ("Alice Ng", "alice@example.com")
    cleared = gui.call("PUT", "/me/profile", "alice", body={"email": ""})
    assert (cleared["display_name"], cleared["email"]) == ("Alice Ng", None)
    assert gui.call("GET", "/me", "alice")["display_name"] == "Alice Ng"
    # A bad email: an ApiError the GUI shows in a toast.
    bad = gui.call("PUT", "/me/profile", "alice", body={"email": "not-an-email"}, status=409)
    fields(bad["error"], {"type", "message", "issues"}, "ApiError body")


def test_import_library_and_definition(gui: Gui, example) -> None:
    sql, ann = example("realization_rate")

    # Import page, step 1: POST /parse {sql, dialect, profile}.
    report = fields(
        gui.call(
            "POST", "/parse", "alice", body={"sql": sql, "dialect": "duckdb", "profile": "demo"}
        ),
        PARSE_REPORT,
        "ParseReport",
    )
    assert report["schema_found"] is True and report["issues"] == []
    literals = each(report["literals"], LITERAL, "LiteralInfo")
    assert "closed_deals.L1" in {lit["id"] for lit in literals}
    typed = [lit for lit in literals if lit["column"] is not None]
    assert typed and fields(typed[0]["column"], COLUMN_REF, "ColumnRef")["type"]
    each(report["ranges"], RANGE, "RangeCandidate")
    each(report["joins"], JOIN, "JoinInfo")
    each(report["ctes"], CTE, "CteInfo")
    grains = each(report["grains"], GRAIN, "GrainCandidate")
    assert grains and grains[0]["id"].endswith(".G1")

    # Step 4: POST /proposals/definitions with the GUI's metadata + annotation shapes.
    outcome = fields(
        gui.call(
            "POST", "/proposals/definitions", "alice", body=gui_proposal(sql, ann, "alice", "demo")
        ),
        PROPOSAL_OUTCOME,
        "ProposalOutcome",
    )
    fields(outcome["proposal"], PROPOSAL_VIEW, "ProposalView")
    assert outcome["auto_applied"] is True and outcome["issues"] == []
    card = fields(outcome["definition"], DEFINITION_CARD, "DefinitionCard")
    assert card["state"] == "llm_annotated" and card["owner"] == "alice"

    # Library (ADR 0026): GET /library?q=&trust=&approval=&created_by=&verified_by=.
    page = fields(
        gui.call("GET", "/library", "alice", query={"q": "realization"}),
        LIBRARY_PAGE,
        "LibraryPage",
    )
    rows = each(page["rows"], LIBRARY_ROW, "LibraryRow")
    assert [r["id"] for r in rows] == ["realization_rate"] and rows[0]["state"] in STATES
    assert rows[0]["created_by"] == "alice" and rows[0]["approval"] == "approved"
    fields(rows[0]["latest_proposal"], PROPOSAL_BRIEF, "ProposalBrief")
    assert page["proposals_visible"] is True and page["pending_count"] == 0
    filtered = _library(gui, "alice", trust=["verified"], approval=["pending", "approved"])
    assert filtered["rows"] == [] and filtered["total"] == 1

    # Definition page: card (+ ?version=), history.
    card = fields(
        gui.call("GET", "/definitions/realization_rate", "alice"), DEFINITION_CARD, "DefinitionCard"
    )
    variables = {v["name"]: fields(v, VARIABLE_CARD, "VariableCard") for v in card["variables"]}
    assert variables["period"]["shape"] == "half_open"
    assert variables["regions"]["allowed"] == ["EMEA", "NA", "APAC", "LATAM"]
    assert isinstance(card["history"], list)
    v1 = gui.call("GET", "/definitions/realization_rate", "alice", query={"version": 1})
    assert v1["version"] == 1
    history = each(
        gui.call("GET", "/definitions/realization_rate/history", "alice"),
        VERSION_INFO,
        "DefinitionVersionInfo",
    )
    assert [h["version"] for h in history] == [1]

    # ADR 0029: no Run tab. The overview reads each variable's original value (no defaults).
    assert variables["period"]["original_value"] == {"start": "2026-01-01", "end": "2026-04-01"}


def test_revise_from_the_definition_page(gui: Gui, example) -> None:
    sql, ann = example("realization_rate")
    gui.call(
        "POST", "/proposals/definitions", "alice", body=gui_proposal(sql, ann, "alice", "demo")
    )

    # Edit tab (DefinitionEditPanel): only the changed metadata keys, no SQL.
    edited = fields(
        gui.call(
            "POST",
            "/proposals/definitions/realization_rate/revise",
            "alice",
            body={
                "base_version": 1,
                "metadata": {"use_when": "cash questions", "keywords": ["rr", "cash"]},
                "summary": "Edit use_when, keywords of realization_rate",
            },
        ),
        PROPOSAL_OUTCOME,
        "ProposalOutcome",
    )
    assert edited["auto_applied"] is True
    card = fields(edited["definition"], DEFINITION_CARD, "DefinitionCard")
    assert card["version"] == 2 and card["use_when"] == "cash questions"
    # ProposalPage (ADR 0029): the query does not change (""); the described fields do,
    # without version / state noise.
    edit_view = fields(
        gui.call(
            "GET", f"/proposals/{edited['proposal']['id']}", "alice", query={"full_diff": True}
        ),
        PROPOSAL_VIEW,
        "ProposalView",
    )
    assert edit_view["sql_diff"] == ""
    changes = each(edit_view["metadata_changes"], METADATA_CHANGE, "MetadataChange")
    assert {c["field"] for c in changes} == {"use_when", "keywords"}

    # Import page in revise mode: GET /definitions/{id}?include_source=true …
    source = fields(
        gui.call("GET", "/definitions/realization_rate", "alice", query={"include_source": True}),
        DEFINITION_CARD | SOURCE,
        "DefinitionCard (include_source)",
    )
    annotations = each(source["annotations"], ANNOTATION, "Annotation")
    assert {a["name"] for a in annotations} == {v["name"] for v in source["variables"]}
    assert gui.call("GET", "/definitions/realization_rate", "alice")["source_sql"] is None
    # … parse the rebuilt SQL (the stored literal IDs must exist in it) …
    report = gui.call(
        "POST",
        "/parse",
        "alice",
        body={"sql": source["source_sql"], "dialect": "duckdb", "profile": "demo"},
    )
    ids = {lit["id"] for lit in report["literals"]} | {g["id"] for g in report["grains"] or []}
    assert all(set(a["literals"]) <= ids for a in annotations)
    # … and POST the revision with SQL, dialect, changed metadata and the annotations.
    revised = fields(
        gui.call(
            "POST",
            "/proposals/definitions/realization_rate/revise",
            "alice",
            body={
                "base_version": 2,
                "sql": source["source_sql"],
                "dialect": "duckdb",
                "metadata": {},
                "annotations": [gui_annotation(a) for a in annotations],
                "summary": "Revise realization_rate (SQL and variables)",
            },
        ),
        PROPOSAL_OUTCOME,
        "ProposalOutcome",
    )
    assert revised["issues"] == [] and revised["definition"]["version"] == 3
    # The same query back: no query change, no described-field change.
    assert revised["proposal"]["sql_diff"] == "" and revised["proposal"]["metadata_changes"] == []
    # A stale base version comes back as a structured 409 the page shows.
    stale = gui.call(
        "POST",
        "/proposals/definitions/realization_rate/revise",
        "alice",
        body={"base_version": 1, "metadata": {"title": "x"}, "summary": ""},
        status=409,
    )
    assert stale["error"]["issues"][0]["code"] == "stale_base_version"


def test_rename_from_the_definition_page(gui: Gui, example) -> None:
    sql, ann = example("realization_rate")
    gui.call(
        "POST", "/proposals/definitions", "alice", body=gui_proposal(sql, ann, "alice", "demo")
    )
    assert composed_definition(gui)["auto_applied"] is True
    gui.call("PUT", "/settings", "root", body={"mode": "strict"})
    pending = gui.call(
        "POST",
        "/proposals/definitions/realization_rate/revise",
        "alice",
        body={"base_version": 1, "metadata": {"grain": "g"}, "summary": "grain"},
    )

    # RenameIdPanel: GET /definitions/{id}/references (ReferencesList) …
    refs = fields(
        gui.call("GET", "/definitions/realization_rate/references", "alice"),
        REFERENCES,
        "DefinitionReferences",
    )
    (used,) = each(refs["used_by"], USED_BY_REF, "UsedByReference")
    assert (used["definition_id"], used["alias"], used["pinned_version"]) == ("rr_apac", "rr", None)
    (proposal,) = each(refs["proposals"], PROPOSAL_REF, "ProposalReference")
    assert proposal["proposal_id"] == pending["proposal"]["id"]
    assert proposal["relation"] == "target" and refs["outside_warning"]
    # … then POST /proposals/definitions/{id}/rename {new_id, base_version, summary}.
    outcome = fields(
        gui.call(
            "POST",
            "/proposals/definitions/realization_rate/rename",
            "alice",
            body={"new_id": "rr_monthly", "base_version": 1, "summary": ""},
        ),
        PROPOSAL_OUTCOME,
        "ProposalOutcome",
    )
    assert outcome["auto_applied"] is False and outcome["proposal"]["status"] == "pending"
    # Review page (ProposalPage): kind rename_definition, payload from/to + references.
    view = fields(
        gui.call("GET", f"/proposals/{outcome['proposal']['id']}", "ann"),
        PROPOSAL_VIEW,
        "ProposalView",
    )
    assert view["kind"] == "rename_definition" and view["diff"] == ""
    assert (view["payload"]["from_id"], view["payload"]["to_id"]) == (
        "realization_rate",
        "rr_monthly",
    )
    fields(view["payload"]["references"], REFERENCES, "DefinitionReferences (payload)")
    approved = gui.call(
        "POST",
        f"/proposals/{view['id']}/approve",
        "ann",
        body={"verify": False, "comment": ""},
    )
    assert approved["definition"]["id"] == "rr_monthly"
    # DefinitionPage: the old id is a 404 naming the new one; the card lists former ids.
    gone = gui.call("GET", "/definitions/realization_rate", "alice", status=404)
    assert "was renamed to 'rr_monthly'" in gone["error"]["message"]
    card = fields(
        gui.call("GET", "/definitions/rr_monthly", "alice"),
        DEFINITION_CARD | {"former_ids"},
        "DefinitionCard",
    )
    assert card["former_ids"] == ["realization_rate"]
    after = gui.call("GET", "/definitions/rr_monthly/references", "alice")
    each(after["former_ids"], FORMER_ID, "FormerId")
    assert after["proposals"][0]["target_id"] == "rr_monthly"
    # The composed definition follows the rename (its part, and its page).
    composed = gui.call("GET", "/definitions/rr_apac", "alice")
    assert composed["composition"]["parts"][0]["id"] == "rr_monthly"


def test_composed_definition_and_join_map(gui: Gui, example) -> None:
    sql, ann = example("realization_rate")
    gui.call(
        "POST", "/proposals/definitions", "alice", body=gui_proposal(sql, ann, "alice", "demo")
    )
    outcome = fields(composed_definition(gui), PROPOSAL_OUTCOME, "ProposalOutcome")
    assert outcome["auto_applied"] is True
    # Proposal page: the composition (parts, fixed/open variables, compiled SQL).
    view = fields(
        gui.call("GET", f"/proposals/{outcome['proposal']['id']}", "alice"),
        PROPOSAL_VIEW,
        "ProposalView",
    )
    composition = fields(view["composition"], COMPOSITION_CARD, "CompositionCard")
    assert composition["compiled_sql"].lstrip().upper().startswith("WITH")

    # Definition page of a composed definition: parts, fixed/open variables, compiled SQL.
    card = fields(
        gui.call("GET", "/definitions/rr_apac", "alice"), DEFINITION_CARD, "DefinitionCard"
    )
    assert card["composed"] is True
    composition = fields(card["composition"], COMPOSITION_CARD, "CompositionCard")
    (part,) = each(composition["parts"], PART_CARD, "PartCard")
    assert (part["alias"], part["id"], part["pinned_version"], part["version"]) == (
        "rr",
        "realization_rate",
        None,
        1,
    )
    assert composition["fixed"] == {"rr": {"regions": ["APAC"]}}
    names = [fields(v, VARIABLE_CARD, "VariableCard")["name"] for v in card["variables"]]
    assert "rr.period" in names and "rr.regions" not in names
    # The part's page: "used by" and "joins to".
    part_card = gui.call("GET", "/definitions/realization_rate", "alice")
    (used,) = each(part_card["used_by"], USED_BY_REF, "UsedByReference")
    assert used["definition_id"] == "rr_apac"
    joins_to = each(part_card["joins"], JOIN_CARD, "JoinCard")
    assert joins_to and all(j["known"] for j in joins_to)
    # Library row: composed, created by alice.
    rows = gui.call("GET", "/library", "alice", query={"q": "rr_apac"})["rows"]
    assert [(r["id"], r["composed"]) for r in rows] == [("rr_apac", True)]

    # Join map page: GET /joins → JoinCard[] (known, confirmed, sources, evidence).
    joins = each(gui.call("GET", "/joins", "alice"), JOIN_CARD, "JoinCard")
    assert joins and all(fields(k, {"left", "right"}, "JoinKey") for j in joins for k in j["keys"])
    edge = next(j for j in joins if j["id"] == "deals__invoices__deal_id=deal_id")
    assert edge["sources"] == ["realization_rate", "rr_apac"] and edge["known"] is True
    # ADR 0029 list view: where each source joins (lib/joins.ts typeVariants, locationLabel)
    # and the sources' states (trust bar) and titles (row detail).
    sources = each(edge["source_joins"], JOIN_SOURCE, "JoinSource")
    assert {s["definition"] for s in sources} == {"realization_rate", "rr_apac"}
    assert all(s["location"] in {"main", "cte", "subquery"} for s in sources)
    assert all(s["join_type"] in {"inner", "left", "right", "full"} for s in sources)
    assert set(edge["source_states"]) == {"realization_rate", "rr_apac"}
    assert set(edge["source_states"].values()) <= STATES
    assert edge["source_titles"]["rr_apac"] == "RR APAC by region"
    by_definition = gui.call("GET", "/joins", "alice", query={"definition": "rr_apac"})
    assert edge["id"] in {j["id"] for j in by_definition}
    by_table = gui.call("GET", "/joins", "alice", query={"table": edge["left_table"]})
    assert edge["id"] in {j["id"] for j in by_table}
    # JoinAnnotateForm (lib/joins.ts annotateRequest): only what changed, by id.
    annotated = fields(
        gui.call(
            "POST",
            "/proposals/joins",
            "alice",
            body={
                "action": "annotate",
                "id": edge["id"],
                "summary": "",
                "relationship": "one_to_many",
                "traps": ["one deal, many invoices"],
                "notes": "partial payments",
            },
        ),
        PROPOSAL_OUTCOME,
        "ProposalOutcome",
    )
    assert annotated["auto_applied"] is True
    evidence = fields(
        annotated["proposal"]["payload"]["evidence"], EVIDENCE, "RelationshipEvidence"
    )
    fields(evidence["left"], SIDE_COUNT, "SideCount")
    after = next(j for j in gui.call("GET", "/joins", "alice") if j["id"] == edge["id"])
    assert after["notes"] == "partial payments" and after["evidence"]["consistent"] is True
    # The GUI adds no joins (ADR 0029); an edge added through propose_join (MCP / API) is
    # listed like any other.
    added = gui.call(
        "POST",
        "/proposals/joins",
        "alice",
        body={
            "action": "add",
            "left_table": "deals",
            "right_table": "invoices",
            "keys": [{"left": "status", "right": "status"}],
            "join_type": "inner",
            "summary": "",
        },
    )
    assert added["auto_applied"] is True and added["proposal"]["payload"]["action"] == "add"
    new_edge = next(
        j for j in gui.call("GET", "/joins", "alice") if j["id"] == "deals__invoices__status=status"
    )
    assert new_edge["added_by"] == "alice" and new_edge["known"] is True
    assert new_edge["sources"] == []
    # Join proposals are approved, never verified: a structured 409.
    gui.call("PUT", "/settings", "root", body={"mode": "strict"})
    pending = gui.call(
        "POST",
        "/proposals/joins",
        "alice",
        body={"action": "annotate", "id": edge["id"], "notes": "x", "summary": ""},
    )
    refused = gui.call(
        "POST",
        f"/proposals/{pending['proposal']['id']}/approve",
        "ann",
        body={"verify": True, "comment": ""},
        status=409,
    )
    assert "not verified" in refused["error"]["message"]
    listed = next(j for j in gui.call("GET", "/joins", "alice") if j["id"] == edge["id"])
    assert listed["pending_proposals"] == [pending["proposal"]["id"]]


def test_review_queue_four_eyes_verify_and_audit(gui: Gui, example) -> None:
    sql, ann = example("realization_rate")
    # Settings page: strict mode, so proposals wait for review.
    settings = fields(
        gui.call("PUT", "/settings", "root", body={"mode": "strict"}), TEAM_SETTINGS, "Settings"
    )
    assert settings["mode"] == "strict"
    assert gui.call("GET", "/settings", "root")["mode"] == "strict"

    created = gui.call(
        "POST", "/proposals/definitions", "alice", body=gui_proposal(sql, ann, "alice", "demo")
    )
    assert created["auto_applied"] is False and created["definition"] is None
    proposal_id = created["proposal"]["id"]

    # Nav badge: GET /proposals/count?status=pending → {count}.
    assert gui.call("GET", "/proposals/count", "alice", query={"status": "pending"}) == {"count": 1}
    # Library (ADR 0026): the pending new definition is a row; "needs my review" for ann.
    mine = gui.call("GET", "/library", "ann", query={"needs_my_review": True})
    (row,) = each(mine["rows"], LIBRARY_ROW, "LibraryRow")
    assert row["version"] is None and row["approval"] == "pending" and row["needs_my_review"]
    (brief,) = each(row["pending"], PROPOSAL_BRIEF, "ProposalBrief")
    assert brief["id"] == proposal_id and mine["pending_count"] == 1
    assert brief["needs_my_review"] is True and brief["lines_added"] > 0
    assert brief["lines_removed"] == 0
    assert gui.call("GET", "/library", "alice", query={"needs_my_review": True})["rows"] == []
    # ADR 0029: "Waiting on you" (oldest first, every filter aside) and its link, the review
    # queue sorted oldest first (lib/library.ts REVIEW_QUEUE_HREF → sort=oldest).
    plain = gui.call("GET", "/library", "ann", query={"q": "no such words"})
    (waiting,) = each(plain["waiting"], WAITING, "WaitingProposal")
    assert (waiting["id"], waiting["target_id"]) == (proposal_id, "realization_rate")
    assert waiting["title"] == row["title"] and waiting["lines_added"] == brief["lines_added"]
    queue = gui.call("GET", "/library", "ann", query={"needs_my_review": True, "sort": "oldest"})
    assert [r["id"] for r in queue["rows"]] == ["realization_rate"]
    assert gui.call("GET", "/library", "alice")["waiting"] == []
    # Review page: list + detail (diff, impact, checks).
    listed = each(
        gui.call("GET", "/proposals", "ann", query={"status": "pending"}),
        PROPOSAL_VIEW,
        "ProposalView",
    )
    assert [p["id"] for p in listed] == [proposal_id]
    # ProposalPage asks for the full-context diff and collapses it itself (lib/diff.ts).
    detail = fields(
        gui.call("GET", f"/proposals/{proposal_id}", "ann", query={"full_diff": True}),
        PROPOSAL_VIEW,
        "ProposalView",
    )
    assert detail["kind"] == "new_definition" and "realization_rate" in detail["diff"]
    assert detail["title"] == row["title"]
    # ADR 0029: the query as written (one hunk with full_diff), all additions for a new
    # definition; the Library's +/- counts are this diff's.
    sql_lines = detail["sql_diff"].splitlines()
    assert sql_lines[0] == "--- /dev/null" and detail["sql_diff"].count("\n@@") == 1
    assert sum(ln.startswith("+") for ln in sql_lines[2:]) == brief["lines_added"]
    assert "version:" not in detail["sql_diff"] and "$period" not in detail["sql_diff"]
    titles = {c["field"]: c for c in each(detail["metadata_changes"], METADATA_CHANGE, "x")}
    assert titles["title"]["before"] is None and titles["title"]["after"] == row["title"]
    assert isinstance(detail["impact"], list)
    each(detail["checks"], {"code", "severity", "message"}, "Issue")

    # Four-eyes (ADR 0018 addendum): the author can't approve their own proposal.
    denied = gui.call(
        "POST",
        f"/proposals/{proposal_id}/approve",
        "alice",
        body={"verify": False, "comment": ""},
        status=403,
    )
    assert denied["error"]["type"] == "PermissionDeniedError"

    # ProposalActions: approve {verify, comment} by a second human → ProposalOutcome.
    approved = fields(
        gui.call(
            "POST",
            f"/proposals/{proposal_id}/approve",
            "ann",
            body={"verify": True, "comment": "checked against finance"},
        ),
        PROPOSAL_OUTCOME,
        "ProposalOutcome",
    )
    assert approved["proposal"]["status"] == "approved"
    assert approved["definition"]["state"] == "verified"
    assert approved["definition"]["verified_by"] == "ann"
    assert gui.call("GET", "/proposals/count", "ann", query={"status": "pending"}) == {"count": 0}

    # Reject {comment} and withdraw → ProposalView.
    sql2, ann2 = example("booked_vs_collected")
    second = gui.call(
        "POST", "/proposals/definitions", "alice", body=gui_proposal(sql2, ann2, "alice", "demo")
    )["proposal"]["id"]
    rejected = fields(
        gui.call("POST", f"/proposals/{second}/reject", "ann", body={"comment": "needs a note"}),
        PROPOSAL_VIEW,
        "ProposalView",
    )
    assert rejected["status"] == "rejected" and rejected["comment"] == "needs a note"
    third = gui.call(
        "POST", "/proposals/definitions", "alice", body=gui_proposal(sql2, ann2, "alice", "demo")
    )["proposal"]["id"]
    withdrawn = fields(
        gui.call("POST", f"/proposals/{third}/withdraw", "alice"), PROPOSAL_VIEW, "ProposalView"
    )
    assert withdrawn["status"] == "withdrawn"

    # Definition page "Verify": POST /definitions/{id}/verify {version} → DefinitionCard.
    gui.call("PUT", "/settings", "root", body={"mode": "open"})
    bvc = gui.call(
        "POST",
        "/proposals/definitions",
        "alice",
        body=gui_proposal(sql2, ann2, "alice", "demo"),
    )
    assert bvc["auto_applied"] is True
    verified = fields(
        gui.call("POST", "/definitions/booked_vs_collected/verify", "ann", body={"version": 1}),
        DEFINITION_CARD,
        "DefinitionCard",
    )
    assert verified["state"] == "verified" and verified["verified_by"] == "ann"

    # Audit page: GET /audit?actor=&kind=&definition_id=&limit= → AuditPage.
    page = fields(
        gui.call(
            "GET",
            "/audit",
            "ann",
            query={"actor": "", "kind": "review", "definition_id": "", "limit": 50},
        ),
        AUDIT_PAGE,
        "AuditPage",
    )
    events = each(page["events"], AUDIT_EVENT, "AuditEvent")
    assert page["limit"] == 50 and events and {e["kind"] for e in events} == {"review"}
    assert {e["outcome"] for e in events} <= {"ok", "error", "rejected"}
    # ADR 0029 Log: the call name is kind + extra.op; a row shows the token's prefix (never
    # the token) and the actors' profiles (email only for callers who may list users).
    assert all(isinstance(e["extra"].get("op"), str) for e in events)
    assert all(e["extra"]["token_prefix"].startswith("cfg_pat_") for e in events)
    assert all(len(e["extra"]["token_prefix"]) == 12 for e in events)
    actors = {name: fields(a, AUDIT_ACTOR, "AuditActor") for name, a in page["actors"].items()}
    assert {e["actor"] for e in events} <= set(actors)
    assert all(a["email"] is None for a in actors.values())  # ann may not list users
    gui.call("PUT", "/me/profile", "alice", body={"email": "alice@example.com"})
    as_root = gui.call("GET", "/audit", "root", query={"actor": "alice", "limit": 5})
    assert as_root["actors"]["alice"]["email"] == "alice@example.com"


def test_settings_users_and_tokens(gui: Gui) -> None:
    # SettingsPage: GET /tools for the checklists (lib/tools.ts groupCatalog).
    catalog = each(gui.call("GET", "/tools", "alice"), TOOL_INFO, "ToolInfo")
    assert [t["group"] for t in catalog][0] == "use" and len(catalog) == 18
    users = each(gui.call("GET", "/users", "root"), USER_VIEW, "UserView")
    assert {u["name"] for u in users} == {"root", "alice", "ann"}
    for u in users:
        each(u["tokens"], TOKEN_CARD, "TokenCard")
        assert all("token" not in t for t in u["tokens"])

    # CreateUserForm → POST /users {name, role, kind} → 201 {user, token} (toCreatedUser).
    created = gui.call(
        "POST",
        "/users",
        "root",
        body={"name": "claude-agent", "role": "contributor", "kind": "agent"},
        status=201,
    )
    fields(created["user"], USER, "User")
    assert created["user"]["kind"] == "agent" and created["token"].startswith("cfg_pat_")
    # The new token works for the GUI's sign-in check (GET /me with that token).
    gui.tokens["claude-agent"] = created["token"]
    assert gui.call("GET", "/me", "claude-agent")["name"] == "claude-agent"

    # UsersTable "Generate token for X" → POST /users/{name}/tokens (TokenForm body).
    body = {
        "name": "laptop",
        "expires_at": "2099-01-31T23:59:59Z",
        "tool_mode": "only",
        "include": ["search"],
        "exclude": [],
    }
    minted = gui.call("POST", "/users/claude-agent/tokens", "root", body=body, status=201)
    info = fields(minted["info"], TOKEN_CARD, "TokenCard")
    assert info["tools"] == ["search"] and info["prefix"] == minted["token"][:12]
    gui.tokens["laptop"] = minted["token"]
    assert gui.call("GET", "/me", "laptop")["tools"] == ["search"]
    listed = each(gui.call("GET", "/users/claude-agent/tokens", "root"), TOKEN_CARD, "TokenCard")
    assert {t["name"] for t in listed} == {"default", "laptop"}
    # Revoke (UsersTable / TokenList) → DELETE /users/{name}/tokens/{id} → TokenCard.
    revoked = gui.call("DELETE", f"/users/claude-agent/tokens/{info['id']}", "root")
    assert revoked["state"] == "revoked"
    gui.call("GET", "/me", "laptop", status=401)

    # ADR 0029: UsersTable "Profile" → PATCH /users/{name} {display_name?, email?}.
    profiled = fields(
        gui.call(
            "PATCH",
            "/users/claude-agent",
            "root",
            body={"display_name": "Claude (agent)", "email": "agent@example.com"},
        ),
        USER_VIEW,
        "UserView",
    )
    assert (profiled["display_name"], profiled["email"]) == ("Claude (agent)", "agent@example.com")
    listed_users = {u["name"]: u for u in gui.call("GET", "/users", "root")}
    assert listed_users["claude-agent"]["display_name"] == "Claude (agent)"

    # Role / active change: PATCH /users/{name} {role} | {active} → UserView.
    changed = fields(
        gui.call("PATCH", "/users/alice", "root", body={"role": "approver"}), USER_VIEW, "UserView"
    )
    assert changed["role"] == "approver"
    off = gui.call("PATCH", "/users/claude-agent", "root", body={"active": False})
    assert off["active"] is False
    gui.call("GET", "/me", "claude-agent", status=401)
    gui.call("PATCH", "/users/claude-agent", "root", body={"active": True})
    # Non-admins get a structured 403 the Settings page can show.
    error = gui.call("GET", "/users", "ann", status=403)["error"]
    assert error["type"] == "PermissionDeniedError"

    # My tokens (MyTokensCard): GET/POST /me/tokens, DELETE /me/tokens/{id}.
    mine = gui.call(
        "POST",
        "/me/tokens",
        "ann",
        body={
            "name": "Claude Code",
            "expires_at": None,
            "tool_mode": "follow_role",
            "include": [],
            "exclude": ["check_and_run"],
        },
        status=201,
    )
    assert set(mine) == {"token", "info"} and "check_and_run" not in mine["info"]["tools"]
    # endpoints.ts setTokenTools → PUT /me/tokens/{id} {tool_mode, include, exclude}.
    retooled = gui.call(
        "PUT",
        f"/me/tokens/{mine['info']['id']}",
        "ann",
        body={"tool_mode": "only", "include": ["search"], "exclude": []},
    )
    assert fields(retooled, TOKEN_CARD, "TokenCard")["tools"] == ["search"]
    own = each(gui.call("GET", "/me/tokens", "ann"), TOKEN_CARD, "TokenCard")
    assert {t["name"] for t in own} == {"default", "Claude Code"}
    assert gui.call("DELETE", f"/me/tokens/{mine['info']['id']}", "ann")["state"] == "revoked"

    # Roles (RolesCard / RoleEditor): GET /roles, POST, PUT, DELETE → 204.
    roles = each(gui.call("GET", "/roles", "root"), ROLE_CARD, "RoleCard")
    assert [r["name"] for r in roles] == ["viewer", "contributor", "approver", "admin"]
    role = gui.call(
        "POST",
        "/roles",
        "root",
        body={
            "name": "support_bot",
            "description": "",
            "allowed": ["search", "compile_and_run"],
            "defaults": ["search"],
            "review_all": False,
        },
        status=201,
    )
    fields(role, ROLE_CARD, "RoleCard")
    # The admin role's editor sends no `allowed` / `review_all` (locked). (Its defaults also
    # narrow root's own follow-role token, so keep manage_role on.)
    fewer = [t["name"] for t in catalog if t["name"] != "check_and_run"]
    edited = gui.call("PUT", "/roles/admin", "root", body={"description": "x", "defaults": fewer})
    assert edited["defaults"] == fewer and len(edited["allowed"]) == 18
    gui.call(
        "PUT",
        "/roles/support_bot",
        "root",
        body={"description": "", "allowed": ["search"], "defaults": ["search"], "review_all": True},
    )
    gui.call("DELETE", "/roles/support_bot", "root", status=204)

    # Settings page, "Default connection" select: PUT /settings {default_connection}.
    shown = fields(gui.call("GET", "/settings", "root"), TEAM_SETTINGS, "Settings")
    assert (shown["default_connection"], shown["effective_default_connection"]) == (None, "demo")
    saved = gui.call("PUT", "/settings", "root", body={"default_connection": "demo"})
    assert saved["default_connection"] == "demo" and saved["issues"] == []
    assert saved["mode"] == shown["mode"]  # only the field given changes
    gui.call("PUT", "/settings", "root", body={"default_connection": "nope"}, status=404)
    gui.call("PUT", "/settings", "ann", body={"default_connection": None}, status=403)
    # Import page: GET /profiles marks it `default`; a parse without a dialect uses its.
    assert gui.call("GET", "/profiles", "ann") == [
        {"name": "demo", "dialect": "duckdb", "default": True}
    ]
    sql = "SELECT 1 AS x FROM deals WHERE status = 'won'"
    assert gui.call("POST", "/parse", "ann", body={"sql": sql})["dialect"] == "duckdb"
    cleared = gui.call("PUT", "/settings", "root", body={"default_connection": None})
    assert cleared["default_connection"] is None
