"""Renaming a definition's ID (ADR 0024, DESIGN §15, §18): references, the rename proposal,
apply everywhere in one transaction, former IDs (search + "renamed to" errors)."""

from __future__ import annotations

import datetime as dt
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from cteforge.domain.audit import AuditKind
from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import (
    CodecError,
    NotFoundError,
    PermissionDeniedError,
    PolicyError,
    StaleVersionError,
)
from cteforge.domain.issues import IssueCode
from cteforge.domain.models import DefinitionState, TeamMode
from cteforge.domain.proposals import ProposalKind, ProposalStatus
from cteforge.domain.users import ActorKind, User, UserRole
from cteforge.services import describe, review
from cteforge.services.context import AppConfig, AppContext, open_context, set_team_mode
from cteforge.services.propose import propose_definition, rename_definition, revise_definition
from cteforge.services.propose_other import propose_recipe
from cteforge.services.revalidate import revalidate
from cteforge.services.run import compile_and_run
from cteforge.services.search import search

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"
FIXED = dt.datetime(2026, 11, 16, 9, 30, tzinfo=dt.UTC)
SQL = (EXAMPLES / "queries" / "realization_rate.sql").read_text()
ANN = yaml.safe_load((EXAMPLES / "definitions" / "realization_rate.annotations.yml").read_text())
META_KEYS = ("id", "title", "meaning", "keywords", "use_when", "dont_use_when", "grain")
OLD, NEW = "realization_rate", "realization_rate_monthly"
RENAMED = f"Definition '{OLD}' was renamed to '{NEW}' on 2026-11-16; use the new ID."

ALICE = User(name="alice", role=UserRole.CONTRIBUTOR)
BOT = User(name="bot", role=UserRole.CONTRIBUTOR, kind=ActorKind.AGENT)
APPROVER = User(name="ann", role=UserRole.APPROVER)
AGENT_APPROVER = User(name="robo", role=UserRole.APPROVER, kind=ActorKind.AGENT)


@pytest.fixture(scope="module")
def warehouse_copy(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("wh") / "warehouse.duckdb"
    shutil.copy(EXAMPLES / "warehouse.duckdb", path)
    return path


@pytest.fixture
def ctx(warehouse_copy: Path) -> Iterator[AppContext]:
    profile = WarehouseProfile(
        name="demo", dialect=Dialect.DUCKDB, options={"path": str(warehouse_copy)}
    )
    config = AppConfig(db_url="sqlite://")
    with open_context(config, profiles=[profile], clock=lambda: FIXED) as c:
        yield c


def _propose(ctx: AppContext, user: User = ALICE, **meta: Any):
    return propose_definition(
        ctx,
        user,
        sql=SQL,
        dialect="duckdb",
        metadata={k: ANN[k] for k in META_KEYS} | {"join_keys": ["month", "region"]} | meta,
        annotations=[dict(a) for a in ANN["variables"]],
    )


def _recipe(ctx: AppContext, id: str = "rr_emea", version: int | None = None) -> None:
    uses = {"rr": {"id": OLD} | ({"version": version} if version else {})}
    recipe = {
        "id": id,
        "title": "Realization EMEA",
        "uses": uses,
        "values": {"rr": {"regions": ["EMEA"]}},
        "dialect": "duckdb",
    }
    assert propose_recipe(ctx, BOT, recipe=recipe).auto_applied


@pytest.fixture
def library(ctx: AppContext) -> AppContext:
    _propose(ctx)
    revise_definition(ctx, ALICE, id=OLD, base_version=1, metadata={"keywords": ["rr"]})
    _recipe(ctx)
    _recipe(ctx, "rr_pinned", version=1)
    return ctx


# ── references ────────────────────────────────────────────────────────────────────────


def test_references_list_recipes_and_pending_proposals(library: AppContext) -> None:
    ctx = library
    set_team_mode(ctx, TeamMode.STRICT)
    pending = revise_definition(ctx, BOT, id=OLD, base_version=2, metadata={"grain": "x"})
    refs = describe.definition_references(ctx, ALICE, OLD)
    assert (refs.id, refs.version) == (OLD, 2)
    assert [(r.recipe_id, r.alias, r.pinned_version) for r in refs.recipes] == [
        ("rr_emea", "rr", None),
        ("rr_pinned", "rr", 1),
    ]
    assert [(p.proposal_id, p.relation) for p in refs.proposals] == [
        (pending.proposal.id, "target")
    ]
    assert refs.former_ids == [] and "outside cteforge" in refs.outside_warning
    with pytest.raises(NotFoundError, match="'nope' does not exist"):
        describe.definition_references(ctx, ALICE, "nope")


# ── apply ─────────────────────────────────────────────────────────────────────────────


def test_open_mode_rename_applies_everywhere_without_a_new_version(library: AppContext) -> None:
    ctx = library
    out = rename_definition(ctx, BOT, id=OLD, new_id=NEW, summary="clearer name")
    assert out.auto_applied and out.proposal.status is ProposalStatus.APPROVED
    assert out.proposal.kind is ProposalKind.RENAME_DEFINITION
    assert out.proposal.payload["from_id"] == OLD and out.proposal.payload["to_id"] == NEW
    assert out.proposal.target_id == NEW and out.proposal.resulting_version is None
    assert out.definition is not None and (out.definition.id, out.definition.version) == (NEW, 2)
    # Every version moved, its YAML rewritten; states, authors and numbers kept.
    assert ctx.store.get_definition(OLD) is None and ctx.store.latest_version(OLD) is None
    assert [h.id for h in ctx.store.definition_history(NEW)] == [NEW, NEW]
    assert ctx.store.get_definition(NEW, 1).id == NEW
    assert ctx.store.get_definition(NEW).state is DefinitionState.LLM_ANNOTATED
    # Recipes point at the new id (pins kept) and still run.
    assert ctx.store.get_recipe("rr_emea").uses["rr"].id == NEW
    assert ctx.store.get_recipe("rr_pinned").uses["rr"].model_dump() == {"id": NEW, "version": 1}
    assert compile_and_run(ctx, BOT, recipe_id="rr_emea").ran
    assert compile_and_run(ctx, BOT, recipe_id="rr_pinned").definitions == [f"{NEW}@1"]
    # The old id is recorded with who and when.
    (former,) = ctx.store.former_ids(NEW)
    assert (former.former_id, former.renamed_by, former.renamed_at) == (OLD, "bot", FIXED)
    assert former.proposal_id == out.proposal.id
    card = describe.get_definition(ctx, BOT, NEW, include_search_document=True)
    assert card.former_ids == [OLD]
    assert f"Definition `{NEW}`. Formerly `{OLD}`." in (card.search_document or "")
    # Audit: the rename is listed under both ids; earlier events keep the old id.
    event = ctx.audit.list_events(kind=AuditKind.PROPOSE)[0]
    assert event.extra["renamed_from"] == OLD and event.extra["renamed_to"] == NEW
    assert event.definitions == [f"{OLD}@2", f"{NEW}@2"]
    assert ctx.audit.list_events(definition_id=OLD)[-1].definitions == [f"{OLD}@1"]


def test_a_former_id_fails_naming_the_new_one_in_every_lookup(library: AppContext) -> None:
    ctx = library
    rename_definition(ctx, ALICE, id=OLD, new_id=NEW)
    calls = [
        lambda: describe.get_definition(ctx, BOT, OLD),
        lambda: describe.get_definition(ctx, BOT, OLD, version=1),
        lambda: describe.definition_history(ctx, BOT, OLD),
        lambda: describe.search_document(ctx, BOT, OLD),
        lambda: describe.definition_references(ctx, BOT, OLD),
        lambda: compile_and_run(ctx, BOT, uses={"rr": {"id": OLD}}),
        lambda: compile_and_run(ctx, BOT, uses={"rr": {"id": OLD, "version": 1}}),
        lambda: revise_definition(ctx, BOT, id=OLD, base_version=2, metadata={"grain": "g"}),
        lambda: review.verify_definition(ctx, APPROVER, OLD, 2),
        lambda: revalidate(ctx, BOT, [OLD]),
        lambda: rename_definition(ctx, BOT, id=OLD, new_id="other_name"),
    ]
    for call in calls:
        with pytest.raises(NotFoundError) as info:
            call()
        assert str(info.value) == RENAMED
    # A recipe proposal using the old id gets the same message as a not_found issue.
    bad = propose_recipe(
        ctx,
        BOT,
        recipe={"id": "r2", "title": "t", "uses": {"rr": {"id": OLD}}, "dialect": "duckdb"},
    )
    assert [(i.code, i.message) for i in bad.issues] == [(IssueCode.NOT_FOUND, f"'rr': {RENAMED}")]


def test_keyword_search_finds_the_old_name(library: AppContext) -> None:
    ctx = library
    _propose(ctx, id="other_rate", title="Collections", meaning="m", keywords=[])
    rename_definition(ctx, ALICE, id=OLD, new_id="rr_by_month", summary="")
    hits = search(library, BOT, "realization_rate")
    assert hits[0].id == "rr_by_month"
    assert "former_ids" in hits[0].matched_fields


def test_chains_report_the_current_id_and_a_rename_can_be_undone(library: AppContext) -> None:
    ctx = library
    rename_definition(ctx, ALICE, id=OLD, new_id="b_rate")
    rename_definition(ctx, ALICE, id="b_rate", new_id="c_rate")
    with pytest.raises(NotFoundError, match="'realization_rate' was renamed to 'c_rate'"):
        describe.get_definition(ctx, BOT, OLD)
    with pytest.raises(NotFoundError, match="'b_rate' was renamed to 'c_rate'"):
        describe.get_definition(ctx, BOT, "b_rate")
    assert [(f.former_id, f.renamed_to) for f in ctx.store.former_ids("c_rate")] == [
        (OLD, "b_rate"),
        ("b_rate", "c_rate"),
    ]
    # Taking back its own former id undoes the rename; that id is no longer "former".
    rename_definition(ctx, ALICE, id="c_rate", new_id=OLD)
    assert describe.get_definition(ctx, BOT, OLD).former_ids == ["b_rate", "c_rate"]


# ── validation ────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["Has Caps", "1abc", "a__b", "a-b", ""])
def test_new_id_must_be_a_valid_slug(library: AppContext, bad: str) -> None:
    with pytest.raises(CodecError, match="not a valid ID"):
        rename_definition(library, ALICE, id=OLD, new_id=bad)


def test_new_id_must_be_free(library: AppContext) -> None:
    ctx = library
    _propose(ctx, id="taken", title="Taken")
    with pytest.raises(PolicyError, match="'taken' already exists"):
        rename_definition(ctx, ALICE, id=OLD, new_id="taken")
    with pytest.raises(PolicyError, match="already the definition's ID"):
        rename_definition(ctx, ALICE, id=OLD, new_id=OLD)
    with pytest.raises(NotFoundError, match="'missing' does not exist"):
        rename_definition(ctx, ALICE, id="missing", new_id="x")
    with pytest.raises(StaleVersionError):
        rename_definition(ctx, ALICE, id=OLD, new_id="x", base_version=1)
    # A former id can be taken neither by another definition nor by a new one.
    rename_definition(ctx, ALICE, id="taken", new_id="taken_now")
    with pytest.raises(PolicyError, match="'taken' is a former ID of definition 'taken_now'"):
        rename_definition(ctx, ALICE, id=OLD, new_id="taken")
    with pytest.raises(PolicyError, match="former ID can't be reused"):
        _propose(ctx, id="taken", title="Again")


def test_revise_with_new_id_is_a_rename_and_nothing_else(library: AppContext) -> None:
    ctx = library
    with pytest.raises(PolicyError, match="its own proposal"):
        revise_definition(ctx, BOT, id=OLD, base_version=2, new_id=NEW, metadata={"grain": "x"})
    with pytest.raises(PolicyError, match="propose a rename"):
        revise_definition(ctx, BOT, id=OLD, base_version=2, metadata={"id": NEW})
    out = revise_definition(ctx, BOT, id=OLD, base_version=2, new_id=NEW)
    assert out.proposal.kind is ProposalKind.RENAME_DEFINITION and out.auto_applied


# ── policy ────────────────────────────────────────────────────────────────────────────


def test_strict_mode_waits_and_the_reviewer_sees_references(library: AppContext) -> None:
    ctx = library
    set_team_mode(ctx, TeamMode.STRICT)
    out = rename_definition(ctx, BOT, id=OLD, new_id=NEW)
    assert not out.auto_applied and out.proposal.status is ProposalStatus.PENDING
    assert ctx.store.get_definition(OLD) is not None  # nothing changed yet
    view = review.get_proposal(ctx, APPROVER, out.proposal.id)
    assert view.impact == ["rr_emea", "rr_pinned"] and view.diff == ""
    refs = view.payload["references"]
    assert [r["recipe_id"] for r in refs["recipes"]] == ["rr_emea", "rr_pinned"]
    with pytest.raises(PolicyError, match="nothing to verify"):
        review.approve(ctx, APPROVER, out.proposal.id, verify=True)
    applied = review.approve(ctx, APPROVER, out.proposal.id)
    assert applied.proposal.status is ProposalStatus.APPROVED
    assert applied.proposal.target_id == NEW and applied.definition.id == NEW
    assert ctx.store.former_ids(NEW)[0].renamed_by == "ann"


def test_verified_definition_needs_a_human_and_four_eyes(library: AppContext) -> None:
    ctx = library
    review.verify_definition(ctx, APPROVER, OLD, 2)
    out = rename_definition(ctx, APPROVER, id=OLD, new_id=NEW)  # open mode, but verified
    assert not out.auto_applied and out.proposal.status is ProposalStatus.PENDING
    with pytest.raises(PermissionDeniedError):
        review.approve(ctx, AGENT_APPROVER, out.proposal.id)
    with pytest.raises(PermissionDeniedError, match="four-eyes"):
        review.approve(ctx, APPROVER, out.proposal.id)
    other = User(name="ben", role=UserRole.APPROVER)
    applied = review.approve(ctx, other, out.proposal.id)
    # Verification survives a rename: it is the same definition and SQL.
    assert applied.definition.state is DefinitionState.VERIFIED
    assert applied.definition.verified_by == "ann"


def test_pending_proposals_follow_the_rename(library: AppContext) -> None:
    ctx = library
    set_team_mode(ctx, TeamMode.STRICT)
    revision = revise_definition(ctx, BOT, id=OLD, base_version=2, metadata={"grain": "g"})
    recipe = propose_recipe(
        ctx,
        BOT,
        recipe={"id": "r3", "title": "t", "uses": {"x": {"id": OLD}}, "dialect": "duckdb"},
    )
    first = rename_definition(ctx, ALICE, id=OLD, new_id=NEW)
    second = rename_definition(ctx, ALICE, id=OLD, new_id="other_name")
    review.approve(ctx, APPROVER, first.proposal.id)
    moved = ctx.store.get_proposal(revision.proposal.id)
    assert moved.target_id == NEW and moved.status is ProposalStatus.PENDING
    assert yaml.safe_load(moved.payload["definition_yaml"])["id"] == NEW
    assert ctx.store.get_proposal(recipe.proposal.id).payload["recipe"]["uses"]["x"]["id"] == NEW
    # The revision still applies (a rename keeps the version), now under the new id.
    applied = review.approve(ctx, APPROVER, revision.proposal.id)
    assert (applied.definition.id, applied.definition.version) == (NEW, 3)
    assert review.approve(ctx, APPROVER, recipe.proposal.id).proposal.status == "approved"
    # A rename overtaken by another rename is stale: it was asked for the old id.
    with pytest.raises(StaleVersionError, match="has since been renamed to 'realization_rate_m"):
        review.approve(ctx, APPROVER, second.proposal.id)
    assert ctx.store.get_proposal(second.proposal.id).status is ProposalStatus.PENDING


def test_viewer_may_not_propose_a_rename(library: AppContext) -> None:
    viewer = User(name="vic", role=UserRole.VIEWER)
    with pytest.raises(PermissionDeniedError):
        rename_definition(library, viewer, id=OLD, new_id=NEW)
    assert describe.definition_references(library, viewer, OLD).id == OLD
