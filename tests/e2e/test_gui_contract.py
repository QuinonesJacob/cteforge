"""GUI ↔ API contract: every call in web/src/api/endpoints.ts, with the body shapes the GUI
sends (lib/importBuilder.ts, lib/variableForm.ts, pages/*), against the real FastAPI app.
Each response is checked for the fields web/src/api/types.ts declares and the GUI reads.

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
    "verified_at",
    "owner",
    "grain",
    "join_keys",
    "outputs",
    "tables",
    "variables",
    "template",
    "history",
}
# Renames (ADR 0024): GET /definitions/{id}/references, DefinitionCard.former_ids.
REFERENCES = {"id", "version", "state", "recipes", "proposals", "former_ids", "outside_warning"}
RECIPE_REF = {"recipe_id", "recipe_version", "title", "state", "alias", "pinned_version"}
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
RECIPE = {"id", "version", "title", "description", "uses", "values", "select", "dialect", "state"}
JOIN_EDGE = {
    "id",
    "left_table",
    "right_table",
    "keys",
    "join_type",
    "relationship",
    "usage_count",
    "traps",
    "state",
}
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
RUN_OUTCOME = {
    "sql",
    "dialect",
    "label",
    "reasons",
    "usable",
    "issues",
    "columns",
    "rows",
    "row_count",
    "truncated",
    "ran",
    "compile_id",
    "definitions",
    "used",
    "duration_ms",
}
USED = {"id", "version", "state", "verified_by", "verified_at"}
PROPOSAL_VIEW = {
    "id",
    "kind",
    "target_id",
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
    "decided_at",
    "comment",
    "resulting_version",
    "diff",
    "impact",
}
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

BASE_URL = "http://127.0.0.1:8765"
Q3_APAC = {"period": {"start": "2025-07-01", "end": "2025-10-01"}, "regions": ["APAC"]}


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


def gui_run(def_id: str, version: int, values: dict[str, Any], *, run: bool) -> dict[str, Any]:
    """lib/variableForm.ts buildRunRequest (alias = definition id)."""
    return {
        "uses": {def_id: {"id": def_id, "version": version}},
        "values": {def_id: values},
        "run": run,
        "limit": 100,
        "profile": "demo",
    }


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
    me = fields(gui.call("GET", "/me", "alice"), USER, "User")
    assert (me["name"], me["role"], me["kind"]) == ("alice", "contributor", "human")
    profiles = each(
        gui.call("GET", "/profiles", "alice"), {"name", "dialect", "default"}, "Profile"
    )
    assert profiles == [{"name": "demo", "dialect": "duckdb", "default": True}]


def test_import_library_definition_and_run(gui: Gui, example) -> None:
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

    # Library: GET /definitions?q=&include_drafts=&limit=100.
    hits = each(
        gui.call(
            "GET",
            "/definitions",
            "alice",
            query={"q": "realization", "include_drafts": False, "limit": 100},
        ),
        SEARCH_HIT,
        "SearchHit",
    )
    assert [h["id"] for h in hits] == ["realization_rate"] and hits[0]["state"] in STATES
    assert hits[0]["match"] in SEARCH_MATCHES and hits[0]["note"] is None
    each(hits[0]["variables"], VARIABLE_CARD, "VariableCard")
    # An empty search (the Library's initial view) lists the library too; match is null.
    listed = gui.call("GET", "/definitions", "alice", query={"q": "", "limit": 100})
    assert listed and all(h["match"] is None for h in listed)

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

    # RunPanel: compile only, then run (values only for changeable variables).
    compiled = fields(
        gui.call("POST", "/run", "alice", body=gui_run("realization_rate", 1, Q3_APAC, run=False)),
        RUN_OUTCOME,
        "RunOutcome",
    )
    assert compiled["ran"] is False and compiled["rows"] == []
    ran = fields(
        gui.call("POST", "/run", "alice", body=gui_run("realization_rate", 1, Q3_APAC, run=True)),
        RUN_OUTCOME,
        "RunOutcome",
    )
    assert ran["ran"] and ran["row_count"] == 3 and ran["label"] in LABELS
    assert ran["columns"] == ["month", "region", "realization_rate"]
    used = each(ran["used"], USED, "UsedDefinition")
    assert used[0]["id"] == "realization_rate" and used[0]["version"] == 1

    # A bad value: ApiError with structured issues for IssueList.
    bad = gui.call(
        "POST",
        "/run",
        "alice",
        body=gui_run("realization_rate", 1, {"period": {"start": "2025-Q3"}}, run=True),
        status=422,
    )
    error = fields(bad["error"], {"type", "message", "issues"}, "ApiError body")
    assert error["type"] == "VariableValueError"
    each(error["issues"], {"code", "severity", "message"}, "Issue")


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
    recipe = {
        "id": "rr_apac",
        "title": "RR APAC",
        "description": "",
        "uses": {"rr": {"id": "realization_rate"}},
        "values": {"rr": Q3_APAC},
        "dialect": "duckdb",
    }
    gui.call("POST", "/proposals/recipes", "alice", body={"recipe": recipe, "summary": "r"})
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
    (used,) = each(refs["recipes"], RECIPE_REF, "RecipeReference")
    assert (used["recipe_id"], used["alias"], used["pinned_version"]) == ("rr_apac", "rr", None)
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


def test_explore_recipes_compose_joins_tables_and_check(gui: Gui, example) -> None:
    sql, ann = example("realization_rate")
    gui.call(
        "POST", "/proposals/definitions", "alice", body=gui_proposal(sql, ann, "alice", "demo")
    )
    select = "SELECT region, AVG(realization_rate) AS avg_rate FROM rr GROUP BY region"

    # Compose tab: lib/variableForm.ts buildComposeRequest (floating refs, final SELECT).
    composed = fields(
        gui.call(
            "POST",
            "/run",
            "alice",
            body={
                "uses": {"rr": {"id": "realization_rate"}},
                "values": {"rr": Q3_APAC},
                "select": select,
                "run": True,
                "limit": 100,
                "profile": "demo",
            },
        ),
        RUN_OUTCOME,
        "RunOutcome",
    )
    assert composed["ran"] and composed["columns"] == ["region", "avg_rate"]

    # SaveRecipeForm: POST /proposals/recipes {recipe, summary}.
    saved = fields(
        gui.call(
            "POST",
            "/proposals/recipes",
            "alice",
            body={
                "recipe": {
                    "id": "apac_q3_rr",
                    "title": "APAC Q3 realization",
                    "description": "",
                    "uses": {"rr": {"id": "realization_rate"}},
                    "values": {"rr": Q3_APAC},
                    "select": select,
                    "dialect": composed["dialect"],
                },
                "summary": "Recipe apac_q3_rr",
            },
        ),
        PROPOSAL_OUTCOME,
        "ProposalOutcome",
    )
    assert saved["auto_applied"] is True
    # Review page shows payload.recipe for recipe proposals.
    view = gui.call("GET", f"/proposals/{saved['proposal']['id']}", "alice")
    fields(view["payload"]["recipe"], RECIPE, "Recipe (proposal payload)")

    # Recipes tab: GET /recipes?q=&include_drafts= → Recipe[]; run one by id.
    recipes = each(
        gui.call("GET", "/recipes", "alice", query={"q": "", "include_drafts": False}),
        RECIPE,
        "Recipe",
    )
    assert [r["id"] for r in recipes] == ["apac_q3_rr"]
    assert recipes[0]["uses"] == {"rr": {"id": "realization_rate", "version": None}}
    ran = fields(
        gui.call(
            "POST",
            "/run",
            "alice",
            body={"recipe_id": "apac_q3_rr", "values": {}, "run": True, "limit": 100},
        ),
        RUN_OUTCOME,
        "RunOutcome",
    )
    assert ran["ran"] and ran["rows"] == composed["rows"]

    # Join map tab: GET /joins?table=&include_drafts= → JoinEdge[].
    joins = each(
        gui.call("GET", "/joins", "alice", query={"table": "", "include_drafts": False}),
        JOIN_EDGE,
        "JoinEdge",
    )
    assert joins and all(fields(k, {"left", "right"}, "JoinKey") for j in joins for k in j["keys"])
    edge = joins[0]
    by_table = gui.call("GET", "/joins", "alice", query={"table": edge["left_table"]})
    assert edge["id"] in {j["id"] for j in by_table}

    # Tables tab: GET /tables (every library table) and ?name=&name=&profile=.
    tables = each(gui.call("GET", "/tables", "alice"), TABLE_CARD, "TableCard")
    assert "deals" in {t["name"] for t in tables}
    params = [("name", "deals"), ("name", "accounts"), ("profile", "demo")]
    response = gui.client.get(
        "/api/v1/tables",
        params=params,
        headers={"Authorization": f"Bearer {gui.tokens['alice']}"},
    )
    assert response.status_code == 200
    named = each(response.json(), TABLE_CARD, "TableCard")
    assert [t["name"] for t in named] == ["deals", "accounts"]
    each(named[0]["columns"], {"name", "type"}, "ColumnCard")
    assert "realization_rate" in named[0]["used_by"]

    # SQL tab: POST /check {sql, run, limit} → RunOutcome labeled by the join map.
    checked = fields(
        gui.call(
            "POST",
            "/check",
            "alice",
            body={
                "sql": "SELECT region, COUNT(*) AS n FROM accounts GROUP BY region",
                "run": True,
                "limit": 100,
            },
        ),
        RUN_OUTCOME,
        "RunOutcome",
    )
    assert checked["ran"] and checked["label"] in {"new_logic_on_known_joins", "exploratory"}


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
    # Review page: list + detail (diff, impact, checks).
    listed = each(
        gui.call("GET", "/proposals", "ann", query={"status": "pending"}),
        PROPOSAL_VIEW,
        "ProposalView",
    )
    assert [p["id"] for p in listed] == [proposal_id]
    detail = fields(
        gui.call("GET", f"/proposals/{proposal_id}", "ann"), PROPOSAL_VIEW, "ProposalView"
    )
    assert detail["kind"] == "new_definition" and "realization_rate" in detail["diff"]
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
        {"events", "limit", "has_more"},
        "AuditPage",
    )
    events = each(page["events"], AUDIT_EVENT, "AuditEvent")
    assert page["limit"] == 50 and events and {e["kind"] for e in events} == {"review"}
    assert {e["outcome"] for e in events} <= {"ok", "error", "rejected"}


def test_settings_users_and_tokens(gui: Gui) -> None:
    users = each(gui.call("GET", "/users", "root"), USER, "User")
    assert {u["name"] for u in users} == {"root", "alice", "ann"}

    # CreateUserForm → POST /users {name, role, kind} → 201 {user, token} (toCreatedUser).
    created = gui.call(
        "POST",
        "/users",
        "root",
        body={"name": "claude-agent", "role": "contributor", "kind": "agent"},
        status=201,
    )
    fields(created["user"], USER, "User")
    assert created["user"]["kind"] == "agent" and len(created["token"]) >= 40
    # The new token works for the GUI's sign-in check (GET /me with that token).
    gui.tokens["claude-agent"] = created["token"]
    assert gui.call("GET", "/me", "claude-agent")["name"] == "claude-agent"

    # Rotate: POST /users/{name}/token → {name, token}; the old token stops working.
    rotated = gui.call("POST", "/users/claude-agent/token", "root")
    assert rotated["name"] == "claude-agent" and rotated["token"] != created["token"]
    gui.call("GET", "/me", "claude-agent", status=401)

    # Role change: PUT /users/{name}/role {role} → User.
    changed = fields(
        gui.call("PUT", "/users/alice/role", "root", body={"role": "approver"}), USER, "User"
    )
    assert changed["role"] == "approver"
    # Non-admins get a structured 403 the Settings page can show.
    error = gui.call("GET", "/users", "ann", status=403)["error"]
    assert error["type"] == "PermissionDeniedError"

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
