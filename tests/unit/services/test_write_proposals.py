"""propose_definition / revise_definition / review (DESIGN §7, §15, §18; ADR 0007, 0018)."""

from __future__ import annotations

import datetime as dt
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from cteforge.domain.audit import AuditKind, AuditOutcome
from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import (
    AnnotationError,
    NotFoundError,
    PermissionDeniedError,
    PolicyError,
    StaleVersionError,
)
from cteforge.domain.issues import IssueCode
from cteforge.domain.models import DefinitionState, Recipe, TeamMode
from cteforge.domain.proposals import ProposalKind, ProposalStatus
from cteforge.domain.users import ActorKind, User, UserRole
from cteforge.services import review
from cteforge.services.context import AppConfig, AppContext, open_context, set_team_mode
from cteforge.services.propose import propose_definition, revise_definition
from cteforge.services.run import compile_and_run

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"
FIXED = dt.datetime(2026, 11, 16, 9, 30, tzinfo=dt.UTC)
SQL = (EXAMPLES / "queries" / "realization_rate.sql").read_text()
ANN = yaml.safe_load((EXAMPLES / "definitions" / "realization_rate.annotations.yml").read_text())
META_KEYS = ("id", "title", "meaning", "keywords", "use_when", "dont_use_when", "grain")

ALICE = User(name="alice", role=UserRole.CONTRIBUTOR)  # human contributor
BOT = User(name="bot", role=UserRole.CONTRIBUTOR, kind=ActorKind.AGENT)
APPROVER = User(name="ann", role=UserRole.APPROVER)
AGENT_APPROVER = User(name="robo", role=UserRole.APPROVER, kind=ActorKind.AGENT)
VIEWER = User(name="vic", role=UserRole.VIEWER)


@pytest.fixture(scope="module")
def warehouse_copy(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("wh") / "warehouse.duckdb"
    shutil.copy(EXAMPLES / "warehouse.duckdb", path)
    return path


def _open(warehouse: Path | None) -> AppContext:
    profiles = (
        []
        if warehouse is None
        else [
            WarehouseProfile(name="demo", dialect=Dialect.DUCKDB, options={"path": str(warehouse)})
        ]
    )
    return open_context(AppConfig(db_url="sqlite://"), profiles=profiles, clock=lambda: FIXED)


@pytest.fixture
def ctx(warehouse_copy: Path) -> Iterator[AppContext]:
    with _open(warehouse_copy) as c:
        yield c


def _meta(**over: Any) -> dict[str, Any]:
    return {k: ANN[k] for k in META_KEYS} | over


def _annotations() -> list[dict[str, Any]]:
    return [dict(a) for a in ANN["variables"]]


def _propose(ctx: AppContext, user: User = ALICE, **meta: Any):
    return propose_definition(
        ctx, user, sql=SQL, dialect="duckdb", metadata=_meta(**meta), annotations=_annotations()
    )


def _strict(ctx: AppContext) -> None:
    set_team_mode(ctx, TeamMode.STRICT)


# ── propose_definition ────────────────────────────────────────────────────────────────


def test_open_mode_auto_applies_as_llm_annotated(ctx: AppContext) -> None:
    out = _propose(ctx, join_keys=["region"])
    assert out.auto_applied and out.issues == []
    p = out.proposal
    assert p.kind is ProposalKind.NEW_DEFINITION and p.status is ProposalStatus.APPROVED
    assert p.resulting_version == 1 and p.decided_by == "alice" and p.decided_at == FIXED
    stored = ctx.store.get_definition("realization_rate")
    assert stored is not None and stored.version == 1
    assert stored.state is DefinitionState.LLM_ANNOTATED and stored.verified_by is None
    assert stored.profile == "demo" and stored.owner == "alice" and stored.join_keys == ["region"]
    assert out.definition is not None and out.definition.state is DefinitionState.LLM_ANNOTATED
    assert p.diff.startswith("--- /dev/null")
    event = ctx.audit.list_events(kind=AuditKind.PROPOSE)[0]
    assert event.actor == "alice" and event.definitions == ["realization_rate@1"]
    assert event.extra["auto_applied"] is True


def test_sql_in_another_dialect_is_checked_transpiled_and_runs_labeled(ctx: AppContext) -> None:
    # DESIGN §12 / ADR 0013: Postgres SQL against the DuckDB profile is checked on the
    # transpiled SQL (dialect_mismatch warning), not left as an unapprovable error.
    out = propose_definition(
        ctx, ALICE, sql=SQL, dialect="postgres", metadata=_meta(), annotations=_annotations()
    )
    assert [i.code for i in out.issues] == [IssueCode.DIALECT_MISMATCH]
    assert out.auto_applied
    stored = ctx.store.get_definition("realization_rate")
    assert stored is not None and stored.source_dialect is Dialect.POSTGRES
    run = compile_and_run(ctx, ALICE, uses={"rr": {"id": "realization_rate"}})
    assert run.ran and run.dialect is Dialect.DUCKDB
    assert IssueCode.TRANSPILED in {i.code for i in run.issues}


def test_omitted_dialect_is_the_connections(ctx: AppContext) -> None:
    out = propose_definition(ctx, ALICE, sql=SQL, metadata=_meta(), annotations=_annotations())
    assert IssueCode.DIALECT_MISMATCH not in {i.code for i in out.issues}
    assert ctx.store.get_definition("realization_rate").source_dialect is Dialect.DUCKDB
    out = propose_definition(
        ctx,
        ALICE,
        sql=SQL,
        dialect="postgres",
        metadata=_meta(id="rr_pg"),
        annotations=_annotations(),
    )
    assert IssueCode.DIALECT_MISMATCH in {i.code for i in out.issues}
    assert ctx.store.get_definition("rr_pg").source_dialect is Dialect.POSTGRES


def test_omitted_dialect_without_a_connection_is_refused() -> None:
    with _open(None) as c:
        with pytest.raises(PolicyError, match="ask an admin to set a default connection"):
            propose_definition(c, ALICE, sql=SQL, metadata=_meta(), annotations=_annotations())


def test_strict_mode_keeps_it_pending(ctx: AppContext) -> None:
    _strict(ctx)
    out = _propose(ctx)
    assert not out.auto_applied and out.definition is None
    assert out.proposal.status is ProposalStatus.PENDING and out.proposal.resulting_version is None
    assert ctx.store.get_definition("realization_rate") is None


def test_no_warehouse_means_incomplete_checks_and_pending() -> None:
    with _open(None) as c:
        out = _propose(c)
        assert any(i.code is IssueCode.CHECKS_INCOMPLETE for i in out.issues)
        assert out.proposal.status is ProposalStatus.PENDING
        stored_yaml = yaml.safe_load(out.proposal.payload["definition_yaml"])
        assert stored_yaml["state"] == "draft" and "profile" not in stored_yaml


def test_agent_definitions_have_no_default_owner(ctx: AppContext) -> None:
    out = _propose(ctx, BOT)
    assert out.definition is not None and out.definition.owner is None


def test_contributor_may_not_name_someone_else_owner(ctx: AppContext) -> None:
    with pytest.raises(PermissionDeniedError):
        _propose(ctx, BOT, owner="alice")
    assert ctx.store.list_proposals() == []
    rejected = ctx.audit.list_events(kind=AuditKind.PROPOSE)[0]
    assert rejected.outcome is AuditOutcome.REJECTED and rejected.error == "PermissionDeniedError"
    assert _propose(ctx, APPROVER, owner="alice").definition.owner == "alice"


def test_viewer_cannot_propose(ctx: AppContext) -> None:
    with pytest.raises(PermissionDeniedError):
        _propose(ctx, VIEWER)


def test_existing_id_is_refused_with_hint(ctx: AppContext) -> None:
    _propose(ctx)
    with pytest.raises(StaleVersionError, match="revise") as info:
        _propose(ctx)
    assert info.value.issues[0].code is IssueCode.STALE_BASE_VERSION


def test_join_keys_must_be_outputs(ctx: AppContext) -> None:
    with pytest.raises(Exception, match="not output columns"):
        _propose(ctx, join_keys=["nope"])


def test_bad_annotation_raises(ctx: AppContext) -> None:
    anns = _annotations()
    anns[0]["literals"] = ["closed_deals.L99", "closed_deals.L2"]
    with pytest.raises(AnnotationError):
        propose_definition(
            ctx, ALICE, sql=SQL, dialect="duckdb", metadata=_meta(), annotations=anns
        )


def test_type_mismatch_is_an_error_issue_and_never_applies(ctx: AppContext) -> None:
    anns = _annotations()
    anns[3] = {"name": "paid_status", "type": "number", "literals": ["collected.L1"]}
    with pytest.raises(AnnotationError):  # 'paid' is not a number: no template at all
        propose_definition(
            ctx, ALICE, sql=SQL, dialect="duckdb", metadata=_meta(), annotations=anns
        )


# ── revise_definition ─────────────────────────────────────────────────────────────────


def test_metadata_only_revision_keeps_template(ctx: AppContext) -> None:
    v1 = _propose(ctx).definition
    out = revise_definition(
        ctx, ALICE, id="realization_rate", base_version=1, metadata={"keywords": ["cash"]}
    )
    assert out.auto_applied and out.issues == []
    v2 = ctx.store.get_definition("realization_rate")
    assert v2.version == 2 and v2.keywords == ["cash"]
    assert (
        v2.template == v1.template
        and v2.variables == ctx.store.get_definition("realization_rate", 1).variables
    )
    history = ctx.store.definition_history("realization_rate")
    assert [h.logic_changed for h in history] == [True, False]
    assert "+- cash\n" in out.proposal.diff and "-- realization\n" in out.proposal.diff


def test_revision_with_annotations_only_reconstructs_sql(ctx: AppContext) -> None:
    _propose(ctx)
    anns = _annotations()
    anns[1]["role"] = "changeable"  # make won_statuses changeable
    out = revise_definition(ctx, ALICE, id="realization_rate", base_version=1, annotations=anns)
    assert out.issues == [] and out.auto_applied
    v2 = ctx.store.get_definition("realization_rate")
    assert v2.variable("won_statuses").role.value == "changeable"
    assert v2.state is DefinitionState.LLM_ANNOTATED
    assert ctx.store.definition_history("realization_rate")[1].logic_changed


def test_revision_with_new_sql(ctx: AppContext) -> None:
    _propose(ctx)
    new_sql = SQL.replace(
        "ROUND(SUM(COALESCE(c.paid, 0)) / SUM(dr.amount), 4)",
        "ROUND(SUM(COALESCE(c.paid, 0)) / SUM(dr.amount), 2)",
    )
    out = revise_definition(
        ctx, ALICE, id="realization_rate", base_version=1, sql=new_sql, summary="2 decimals"
    )
    assert out.auto_applied
    v2 = ctx.store.get_definition("realization_rate")
    assert "2)" in v2.template and v2.version == 2
    assert ctx.store.definition_history("realization_rate")[1].change_note == "2 decimals"


def test_stale_base_version_at_propose_time(ctx: AppContext) -> None:
    _propose(ctx)
    revise_definition(ctx, ALICE, id="realization_rate", base_version=1, metadata={"keywords": []})
    with pytest.raises(StaleVersionError) as info:
        revise_definition(
            ctx, ALICE, id="realization_rate", base_version=1, metadata={"keywords": ["x"]}
        )
    assert info.value.issues[0].code is IssueCode.STALE_BASE_VERSION
    assert ctx.store.latest_version("realization_rate") == 2


def test_stale_base_version_at_approve_time(ctx: AppContext) -> None:
    _propose(ctx)
    _strict(ctx)
    first = revise_definition(
        ctx, ALICE, id="realization_rate", base_version=1, metadata={"keywords": ["a"]}
    )
    second = revise_definition(
        ctx, ALICE, id="realization_rate", base_version=1, metadata={"keywords": ["b"]}
    )
    review.approve(ctx, APPROVER, first.proposal.id)
    with pytest.raises(StaleVersionError) as info:
        review.approve(ctx, APPROVER, second.proposal.id)
    assert info.value.issues[0].code is IssueCode.STALE_BASE_VERSION
    assert ctx.store.get_proposal(second.proposal.id).status is ProposalStatus.PENDING
    assert ctx.store.get_definition("realization_rate").keywords == ["a"]


def test_revise_unknown_definition(ctx: AppContext) -> None:
    with pytest.raises(NotFoundError):
        revise_definition(ctx, ALICE, id="nope", base_version=1, metadata={"title": "x"})


def test_revise_needs_a_change(ctx: AppContext) -> None:
    _propose(ctx)
    with pytest.raises(PolicyError):
        revise_definition(ctx, ALICE, id="realization_rate", base_version=1)


def test_contributor_cannot_take_over_ownership(ctx: AppContext) -> None:
    _propose(ctx)  # owned by alice
    with pytest.raises(PermissionDeniedError):
        revise_definition(
            ctx, BOT, id="realization_rate", base_version=1, metadata={"owner": "bot"}
        )


def test_revision_of_verified_definition_always_waits(ctx: AppContext) -> None:
    _propose(ctx)
    review.verify_definition(ctx, APPROVER, "realization_rate", 1)
    out = revise_definition(
        ctx, BOT, id="realization_rate", base_version=1, metadata={"keywords": ["x"]}
    )
    assert not out.auto_applied and out.proposal.status is ProposalStatus.PENDING
    assert out.issues == []  # checks passed; the verified target alone blocks auto-apply
    assert yaml.safe_load(out.proposal.payload["definition_yaml"])["state"] == "llm_annotated"
    with pytest.raises(PermissionDeniedError):  # an agent approver may not touch verified
        review.approve(ctx, AGENT_APPROVER, out.proposal.id)
    applied = review.approve(ctx, APPROVER, out.proposal.id, verify=True)
    assert applied.definition.state is DefinitionState.VERIFIED and applied.definition.version == 2
    assert (
        applied.definition.verified_by == "ann" and applied.definition.verified_at == FIXED.date()
    )


# ── review ────────────────────────────────────────────────────────────────────────────


def test_approve_pending_proposal(ctx: AppContext) -> None:
    _strict(ctx)
    pending = _propose(ctx)
    out = review.approve(ctx, APPROVER, pending.proposal.id, comment="ok")
    assert out.proposal.status is ProposalStatus.APPROVED and not out.auto_applied
    assert out.proposal.decided_by == "ann" and out.proposal.comment == "ok"
    stored = ctx.store.get_definition("realization_rate")
    assert stored.state is DefinitionState.LLM_ANNOTATED and stored.verified_by is None
    with pytest.raises(PolicyError):
        review.approve(ctx, APPROVER, pending.proposal.id)


def test_approve_and_verify_by_human_approver(ctx: AppContext) -> None:
    _strict(ctx)
    pending = _propose(ctx)
    out = review.approve(ctx, APPROVER, pending.proposal.id, verify=True)
    stored = ctx.store.get_definition("realization_rate")
    assert stored.state is DefinitionState.VERIFIED
    assert stored.verified_by == "ann" and stored.verified_at == FIXED.date()
    assert out.definition.state is DefinitionState.VERIFIED


def test_agent_approver_can_approve_but_not_verify(ctx: AppContext) -> None:
    _strict(ctx)
    pending = _propose(ctx)
    with pytest.raises(PermissionDeniedError):
        review.approve(ctx, AGENT_APPROVER, pending.proposal.id, verify=True)
    assert ctx.store.get_definition("realization_rate") is None
    out = review.approve(ctx, AGENT_APPROVER, pending.proposal.id)
    assert out.definition.state is DefinitionState.LLM_ANNOTATED


def test_contributor_cannot_approve_new_definition(ctx: AppContext) -> None:
    _strict(ctx)
    pending = _propose(ctx, BOT)
    with pytest.raises(PermissionDeniedError):
        review.approve(ctx, ALICE, pending.proposal.id)
    events = ctx.audit.list_events(kind=AuditKind.REVIEW)
    assert events[0].outcome is AuditOutcome.REJECTED


def test_owner_may_approve_changes_but_not_verify(ctx: AppContext) -> None:
    _propose(ctx)  # alice (contributor) owns it
    _strict(ctx)
    change = revise_definition(
        ctx, BOT, id="realization_rate", base_version=1, metadata={"keywords": ["z"]}
    )
    with pytest.raises(PermissionDeniedError):
        review.approve(ctx, ALICE, change.proposal.id, verify=True)
    out = review.approve(ctx, ALICE, change.proposal.id)
    assert out.definition.version == 2 and out.definition.state is DefinitionState.LLM_ANNOTATED
    with pytest.raises(PermissionDeniedError):
        review.verify_definition(ctx, ALICE, "realization_rate", 2)


def test_errors_block_approval(ctx: AppContext) -> None:
    # A range that isn't a suggested pair without description: description_required error.
    anns = _annotations()
    anns[0] = {
        "name": "period",
        "type": "date_range",
        "role": "changeable",
        "literals": ["closed_deals.L1", "closed_deals.L2"],
    }
    out = propose_definition(
        ctx,
        ALICE,
        sql=SQL.replace("AND d.closed_at <", "OR d.closed_at <"),
        dialect="duckdb",
        metadata=_meta(),
        annotations=anns,
    )
    assert any(i.severity.value == "error" for i in out.issues)
    assert out.proposal.status is ProposalStatus.PENDING
    with pytest.raises(PolicyError, match="failing checks"):
        review.approve(ctx, APPROVER, out.proposal.id)


def test_verify_refused_when_checks_incomplete() -> None:
    with _open(None) as c:
        pending = _propose(c)
        with pytest.raises(PolicyError, match="not fully checked"):
            review.approve(c, APPROVER, pending.proposal.id, verify=True)
        out = review.approve(c, APPROVER, pending.proposal.id)
        assert out.definition.state is DefinitionState.DRAFT
        with pytest.raises(PolicyError, match="draft"):
            review.verify_definition(c, APPROVER, "realization_rate", 1)


def test_reject_needs_comment_and_keeps_it(ctx: AppContext) -> None:
    _strict(ctx)
    pending = _propose(ctx)
    with pytest.raises(PolicyError):
        review.reject(ctx, APPROVER, pending.proposal.id, "  ")
    view = review.reject(ctx, APPROVER, pending.proposal.id, "wrong grain")
    assert view.status is ProposalStatus.REJECTED and view.comment == "wrong grain"
    assert ctx.store.get_proposal(pending.proposal.id).decided_by == "ann"
    with pytest.raises(PermissionDeniedError):
        review.reject(ctx, ALICE, _propose(ctx).proposal.id, "no")


def test_withdraw_author_only(ctx: AppContext) -> None:
    _strict(ctx)
    pending = _propose(ctx, BOT)
    with pytest.raises(PermissionDeniedError):
        review.withdraw(ctx, APPROVER, pending.proposal.id)
    view = review.withdraw(ctx, BOT, pending.proposal.id)
    assert view.status is ProposalStatus.WITHDRAWN
    with pytest.raises(PolicyError):
        review.withdraw(ctx, BOT, pending.proposal.id)


def test_list_and_get_with_diff_and_impact(ctx: AppContext) -> None:
    _propose(ctx)
    ctx.store.add_recipe_version(
        Recipe(
            id="rr_emea",
            title="RR EMEA",
            uses={"rr": {"id": "realization_rate"}},
            values={"rr": {"regions": ["EMEA"]}},
            dialect=Dialect.DUCKDB,
        ),
        author="alice",
    )
    _strict(ctx)
    change = revise_definition(
        ctx, ALICE, id="realization_rate", base_version=1, metadata={"title": "RR"}
    )
    views = review.list_proposals(ctx, VIEWER, ProposalStatus.PENDING)
    assert [v.id for v in views] == [change.proposal.id]
    view = review.get_proposal(ctx, VIEWER, change.proposal.id)
    assert view.impact == ["rr_emea"]
    assert "--- realization_rate@v1" in view.diff and "+title: RR" in view.diff
    assert len(review.list_proposals(ctx, VIEWER)) == 2
    with pytest.raises(NotFoundError):
        review.get_proposal(ctx, VIEWER, "missing")


def test_verify_definition(ctx: AppContext) -> None:
    _propose(ctx)
    with pytest.raises(PermissionDeniedError):
        review.verify_definition(ctx, AGENT_APPROVER, "realization_rate", 1)
    card = review.verify_definition(ctx, APPROVER, "realization_rate", 1)
    assert card.state is DefinitionState.VERIFIED and card.verified_by == "ann"
    assert card.verified_at == FIXED.date()
    change = revise_definition(
        ctx, ALICE, id="realization_rate", base_version=1, metadata={"title": "x"}
    )
    assert change.proposal.status is ProposalStatus.PENDING  # verified: never auto-applied
    review.approve(ctx, APPROVER, change.proposal.id)
    with pytest.raises(StaleVersionError):
        review.verify_definition(ctx, APPROVER, "realization_rate", 1)


# ── regressions (fixer pass) ──────────────────────────────────────────────────────────

BLOB_SQL = "SELECT deal_id FROM deals WHERE status::BLOB = 'closed_won'"
BLOB_ANN = [{"name": "st", "type": "text", "role": "changeable", "literals": ["main.L1"]}]


def _propose_blob(ctx: AppContext):
    return propose_definition(
        ctx,
        ALICE,
        sql=BLOB_SQL,
        dialect="duckdb",
        metadata={"id": "blob_deals", "title": "Blob deals"},
        annotations=BLOB_ANN,
    )


def test_draft_proposal_cannot_be_verified(ctx: AppContext) -> None:
    """Regression: a DRAFT proposal (type_unknown warning, no checks_incomplete) passed the
    verify gate and was stored as VERIFIED."""
    _strict(ctx)
    out = _propose_blob(ctx)
    assert IssueCode.TYPE_UNKNOWN in {i.code for i in out.issues}
    assert yaml.safe_load(out.proposal.payload["definition_yaml"])["state"] == "draft"
    with pytest.raises(PolicyError, match="draft"):
        review.approve(ctx, APPROVER, out.proposal.id, verify=True)
    assert ctx.store.get_definition("blob_deals") is None
    review.approve(ctx, APPROVER, out.proposal.id)  # approving as a draft is allowed
    assert ctx.store.get_definition("blob_deals").state is DefinitionState.DRAFT


def test_draft_proposal_is_never_auto_applied(ctx: AppContext) -> None:
    """Regression: open mode auto-applied a DRAFT proposal as if every check passed."""
    out = _propose_blob(ctx)
    assert not out.auto_applied and out.proposal.status is ProposalStatus.PENDING
    assert ctx.store.get_definition("blob_deals") is None


def test_metadata_revision_of_a_draft_cannot_be_verified(ctx: AppContext) -> None:
    """Regression: a metadata-only revision of a draft carries no issues and was
    verifiable."""
    _strict(ctx)
    review.approve(ctx, APPROVER, _propose_blob(ctx).proposal.id)
    rev = revise_definition(ctx, ALICE, id="blob_deals", base_version=1, metadata={"title": "x"})
    assert rev.issues == []
    with pytest.raises(PolicyError, match="draft"):
        review.approve(ctx, APPROVER, rev.proposal.id, verify=True)
    assert ctx.store.get_definition("blob_deals").version == 1


def test_concurrent_approve_and_reject_only_one_wins(
    warehouse_copy: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: approve and reject both passed the pending check and both succeeded."""
    import threading
    import time

    from cteforge.services.propose_other import propose_join

    profile = WarehouseProfile(
        name="demo", dialect=Dialect.DUCKDB, options={"path": str(warehouse_copy)}
    )
    config = AppConfig(db_url=f"sqlite:///{tmp_path / 'race.db'}")
    with open_context(config, profiles=[profile], clock=lambda: FIXED) as c:
        _strict(c)
        pending = propose_join(
            c,
            ALICE,
            left_table="deals",
            right_table="accounts",
            keys=[["account_id", "account_id"]],
        )
        assert pending.issues == []
        real = review._pending

        def slow_pending(proposal):  # both threads pass the check, then race
            real(proposal)
            time.sleep(0.3)

        monkeypatch.setattr(review, "_pending", slow_pending)
        results: dict[str, str] = {}

        def run(name: str, call) -> None:
            try:
                call()
                results[name] = "ok"
            except PolicyError:
                results[name] = "refused"

        pid = pending.proposal.id
        threads = [
            threading.Thread(
                target=run, args=("approve", lambda: review.approve(c, APPROVER, pid))
            ),
            threading.Thread(
                target=run, args=("reject", lambda: review.reject(c, APPROVER, pid, "no"))
            ),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(results.values()) == ["ok", "refused"]
        final = c.store.get_proposal(pid).status
        edge_applied = c.store.list_joins() != []
        if results["approve"] == "ok":
            assert final is ProposalStatus.APPROVED and edge_applied
        else:
            assert final is ProposalStatus.REJECTED and not edge_applied


# ── four-eyes rule (ADR 0018 addendum) ────────────────────────────────────────────────

BO = User(name="bo", role=UserRole.APPROVER)
ADMIN = User(name="root", role=UserRole.ADMIN)


def test_approver_cannot_approve_own_proposal(ctx: AppContext) -> None:
    _strict(ctx)
    own = _propose(ctx, APPROVER)
    with pytest.raises(PermissionDeniedError, match="four-eyes"):
        review.approve(ctx, APPROVER, own.proposal.id)
    with pytest.raises(PermissionDeniedError, match="four-eyes"):
        review.approve(ctx, APPROVER, own.proposal.id, verify=True)
    assert ctx.store.get_definition("realization_rate") is None
    assert ctx.store.get_proposal(own.proposal.id).status is ProposalStatus.PENDING
    events = ctx.audit.list_events(kind=AuditKind.REVIEW)
    assert events[0].outcome is AuditOutcome.REJECTED
    out = review.approve(ctx, BO, own.proposal.id, verify=True)  # another person may
    assert out.definition.state is DefinitionState.VERIFIED and out.definition.verified_by == "bo"


def test_owner_cannot_approve_own_revision(ctx: AppContext) -> None:
    _propose(ctx)  # alice (contributor) owns it
    _strict(ctx)
    change = revise_definition(
        ctx, ALICE, id="realization_rate", base_version=1, metadata={"keywords": ["z"]}
    )
    with pytest.raises(PermissionDeniedError, match="four-eyes"):
        review.approve(ctx, ALICE, change.proposal.id)
    assert review.approve(ctx, APPROVER, change.proposal.id).definition.version == 2


def test_admin_may_approve_and_verify_own_work(ctx: AppContext) -> None:
    _strict(ctx)
    own = _propose(ctx, ADMIN)
    out = review.approve(ctx, ADMIN, own.proposal.id, verify=True)
    assert out.definition.state is DefinitionState.VERIFIED and out.definition.verified_by == "root"


def test_agent_admin_may_not_approve_or_verify_own_work(ctx: AppContext) -> None:
    """Regression (ADR 0018 addendum): the admin exemption is for human admins only."""
    agent_admin = User(name="botadmin", role=UserRole.ADMIN, kind=ActorKind.AGENT)
    _strict(ctx)
    own = _propose(ctx, agent_admin)
    with pytest.raises(PermissionDeniedError, match="four-eyes"):
        review.approve(ctx, agent_admin, own.proposal.id)
    with pytest.raises(PermissionDeniedError):
        review.approve(ctx, agent_admin, own.proposal.id, verify=True)
    assert ctx.store.get_proposal(own.proposal.id).status is ProposalStatus.PENDING
    review.approve(ctx, BO, own.proposal.id)
    with pytest.raises(PermissionDeniedError):
        review.verify_definition(ctx, agent_admin, "realization_rate", 1)


def test_cannot_verify_a_version_you_authored(ctx: AppContext) -> None:
    _strict(ctx)
    own = _propose(ctx, APPROVER)  # version 1 will be authored by ann
    review.approve(ctx, BO, own.proposal.id)  # approved, not verified
    assert ctx.store.definition_history("realization_rate")[0].author == "ann"
    with pytest.raises(PermissionDeniedError, match="four-eyes"):
        review.verify_definition(ctx, APPROVER, "realization_rate", 1)
    assert ctx.store.get_definition("realization_rate").state is DefinitionState.LLM_ANNOTATED
    card = review.verify_definition(ctx, BO, "realization_rate", 1)
    assert card.state is DefinitionState.VERIFIED and card.verified_by == "bo"


def test_verify_definition_author_is_per_version(ctx: AppContext) -> None:
    _propose(ctx)  # v1 by alice, auto-applied
    _strict(ctx)
    change = revise_definition(
        ctx, APPROVER, id="realization_rate", base_version=1, metadata={"keywords": ["y"]}
    )
    review.approve(ctx, BO, change.proposal.id)  # v2 authored by ann
    with pytest.raises(PermissionDeniedError):
        review.verify_definition(ctx, APPROVER, "realization_rate", 2)
    assert review.verify_definition(ctx, BO, "realization_rate", 2).verified_by == "bo"


# ── verify only on complete, passing checks (defense in depth in _apply) ──────────────


def test_apply_proposal_refuses_verify_on_incomplete_checks() -> None:
    from cteforge.services import _apply

    with _open(None) as c:
        pending = _propose(c)
        stored = c.store.get_proposal(pending.proposal.id)
        assert _apply.proposed_state(stored) is DefinitionState.DRAFT
        with pytest.raises(PolicyError, match="not fully checked"):
            _apply.apply_proposal(c, stored, APPROVER, auto=False, verify=True)
        assert c.store.get_definition("realization_rate") is None
        assert c.store.get_proposal(stored.id).status is ProposalStatus.PENDING


def test_apply_proposal_refuses_verify_on_errors_and_drafts(ctx: AppContext) -> None:
    from cteforge.domain.issues import Issue, Severity
    from cteforge.services import _apply

    _strict(ctx)
    clean = ctx.store.get_proposal(_propose(ctx).proposal.id)
    assert _apply.verify_refusal(clean) is None
    error = Issue(code=IssueCode.NOT_FOUND, severity=Severity.ERROR, message="x")
    with pytest.raises(PolicyError, match="failing checks"):
        _apply.apply_proposal(
            ctx, clean.model_copy(update={"checks": [error]}), APPROVER, auto=False, verify=True
        )
    draft_use = Issue(code=IssueCode.DRAFT_NOT_USABLE, severity=Severity.WARNING, message="x")
    assert "draft definition" in _apply.verify_refusal(
        clean.model_copy(update={"checks": [draft_use]})
    )
    blob = ctx.store.get_proposal(_propose_blob(ctx).proposal.id)
    with pytest.raises(PolicyError, match="draft"):
        _apply.apply_proposal(ctx, blob, APPROVER, auto=False, verify=True)
    assert ctx.store.get_definition("realization_rate") is None
    assert ctx.store.get_definition("blob_deals") is None


# ── compare-and-swap across processes ─────────────────────────────────────────────────


def test_two_processes_cannot_both_decide(
    warehouse_copy: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two contexts on one database stand in for two server processes: the in-process
    lock does not help, the store's compare-and-swap does."""
    from cteforge.services import _apply

    profile = WarehouseProfile(
        name="demo", dialect=Dialect.DUCKDB, options={"path": str(warehouse_copy)}
    )
    config = AppConfig(db_url=f"sqlite:///{tmp_path / 'two.db'}")
    with (
        open_context(config, profiles=[profile], clock=lambda: FIXED) as a,
        open_context(config, profiles=[profile], clock=lambda: FIXED) as b,
    ):
        _strict(a)
        pid = _propose(a).proposal.id
        review.reject(a, APPROVER, pid, "wrong grain")
        # Process b read the proposal while it was still pending: skip its pending checks.
        monkeypatch.setattr(review, "_pending", lambda proposal: None)
        monkeypatch.setattr(_apply, "still_pending", lambda ctx, id: ctx.store.get_proposal(id))
        stale = a.store.get_proposal(pid).model_copy(update={"status": ProposalStatus.PENDING})
        monkeypatch.setattr(review, "_get", lambda ctx, id: stale)
        with pytest.raises(PolicyError, match="rejected meanwhile"):
            review.approve(b, BO, pid)
        assert b.store.get_definition("realization_rate") is None
        final = b.store.get_proposal(pid)
        assert final.status is ProposalStatus.REJECTED and final.decided_by == "ann"


def test_failed_write_releases_the_claim(ctx: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:
    from cteforge.domain.errors import StoreError

    _strict(ctx)
    pid = _propose(ctx).proposal.id

    def broken(*args: Any, **kwargs: Any) -> None:
        raise StoreError("disk full")

    monkeypatch.setattr(ctx.store, "add_definition_version", broken)
    with pytest.raises(StoreError):
        review.approve(ctx, APPROVER, pid)
    stored = ctx.store.get_proposal(pid)
    assert stored.status is ProposalStatus.PENDING and stored.resulting_version is None
    monkeypatch.undo()
    assert review.approve(ctx, APPROVER, pid).definition.version == 1


# ── four-eyes across metadata-only versions ──────────────────────────────────────────


def test_metadata_only_revision_does_not_launder_the_logic_author(ctx: AppContext) -> None:
    """ann writes the logic (v1); an agent's metadata-only v2 (auto-applied in open mode)
    must not let ann verify her own unreviewed SQL."""
    first = _propose(ctx, APPROVER)  # v1 by ann, auto-applied
    assert first.auto_applied
    with pytest.raises(PermissionDeniedError, match="four-eyes"):
        review.verify_definition(ctx, APPROVER, "realization_rate", 1)
    cosmetic = revise_definition(
        ctx, BOT, id="realization_rate", base_version=1, metadata={"keywords": ["count"]}
    )
    assert cosmetic.auto_applied
    history = ctx.store.definition_history("realization_rate")
    assert (history[-1].author, history[-1].logic_changed) == ("bot", False)
    with pytest.raises(PermissionDeniedError, match="four-eyes"):
        review.verify_definition(ctx, APPROVER, "realization_rate", 2)
    assert ctx.store.get_definition("realization_rate").state is DefinitionState.LLM_ANNOTATED
    assert review.verify_definition(ctx, BO, "realization_rate", 2).verified_by == "bo"


def test_approve_with_verify_of_metadata_only_revision_checks_the_logic_author(
    ctx: AppContext,
) -> None:
    _propose(ctx, APPROVER)  # v1 logic by ann
    _strict(ctx)
    cosmetic = revise_definition(
        ctx, BOT, id="realization_rate", base_version=1, metadata={"keywords": ["count"]}
    )
    assert not cosmetic.auto_applied
    with pytest.raises(PermissionDeniedError, match="four-eyes"):
        review.approve(ctx, APPROVER, cosmetic.proposal.id, verify=True)
    assert ctx.store.get_proposal(cosmetic.proposal.id).status is ProposalStatus.PENDING
    out = review.approve(ctx, BO, cosmetic.proposal.id, verify=True)
    assert out.definition.state is DefinitionState.VERIFIED and out.definition.verified_by == "bo"


def test_a_logic_change_starts_a_new_authorship(ctx: AppContext) -> None:
    """Once someone else rewrites the logic, the earlier author reviews it like anyone."""
    _propose(ctx, APPROVER)  # v1 by ann
    changed_sql = SQL.replace("ROUND(", "ROUND(1.0 * ", 1)
    rewrite = revise_definition(ctx, ALICE, id="realization_rate", base_version=1, sql=changed_sql)
    assert rewrite.auto_applied, rewrite.issues
    assert ctx.store.definition_history("realization_rate")[-1].logic_changed
    assert review.verify_definition(ctx, APPROVER, "realization_rate", 2).verified_by == "ann"


# ── proposal checks never leak data through driver errors ────────────────────────────


def test_strict_mode_proposal_checks_do_not_echo_runtime_error_text(ctx: AppContext) -> None:
    """Strict mode refuses exploratory SQL; a proposal's check run must not become a way to
    read table contents through a failing cast's error message (stored on the proposal,
    readable by any viewer)."""
    from cteforge.services.run import check_and_run

    _strict(ctx)
    refused = check_and_run(ctx, BOT, "SELECT name FROM accounts")
    assert not refused.usable and not refused.ran
    out = propose_definition(
        ctx,
        BOT,
        sql="SELECT CAST((SELECT string_agg(name, '|') FROM accounts) AS INTEGER) AS x",
        dialect="duckdb",
        metadata={"id": "exfil", "title": "x"},
        annotations=[],
    )
    failed = [i for i in out.issues if i.code is IssueCode.COMPILE_FAILED]
    assert failed and "Conversion Error" in failed[0].message
    names = [r[0] for r in ctx.warehouses.get("demo").run("SELECT name FROM accounts", 5).rows]
    stored = review.list_proposals(ctx, VIEWER, ProposalStatus.PENDING)
    for text in [*(i.message for i in out.issues), stored[0].model_dump_json()]:
        assert not any(name in text for name in names), text


def test_compile_errors_in_proposal_checks_keep_their_message(ctx: AppContext) -> None:
    out = propose_definition(
        ctx,
        BOT,
        sql="SELECT no_such_column FROM accounts",
        dialect="duckdb",
        metadata={"id": "typo", "title": "x"},
        annotations=[],
    )
    messages = " ".join(i.message for i in out.issues)
    assert "no_such_column" in messages  # schema facts help fix the SQL


# ── read-only SQL only ────────────────────────────────────────────────────────────────


def _no_checks(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("checks ran for non-read-only SQL")


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM sales WHERE region = 'APAC'",
        "WITH gone AS (DELETE FROM sales RETURNING *) SELECT * FROM gone",
        "SELECT * INTO copy_of_sales FROM sales",
    ],
)
def test_propose_refuses_non_read_only_sql_before_checks(
    ctx: AppContext, monkeypatch: pytest.MonkeyPatch, sql: str
) -> None:
    from cteforge.services import _apply

    monkeypatch.setattr(_apply, "import_query", _no_checks)
    with pytest.raises(PolicyError) as info:
        propose_definition(ctx, ALICE, sql=sql, dialect="duckdb", metadata=_meta(), annotations=[])
    assert [i.code for i in info.value.issues] == [IssueCode.NOT_READ_ONLY]
    assert ctx.store.list_proposals() == []
    assert ctx.audit.list_events(kind=AuditKind.PROPOSE)[0].outcome is AuditOutcome.REJECTED


def test_revise_refuses_non_read_only_sql(ctx: AppContext) -> None:
    _propose(ctx)
    with pytest.raises(PolicyError) as info:
        revise_definition(
            ctx,
            ALICE,
            id="realization_rate",
            base_version=1,
            sql="UPDATE sales SET amount = 0",
            annotations=[],
        )
    assert [i.code for i in info.value.issues] == [IssueCode.NOT_READ_ONLY]
    assert ctx.store.latest_version("realization_rate") == 1


# ── admin agents (ADR 0027) ──────────────────────────────────────────────────────────

AGENT_ADMIN = User(name="botadmin", role=UserRole.ADMIN, kind=ActorKind.AGENT)


def test_agent_admin_approves_verifies_and_changes_verified_work_of_others(
    ctx: AppContext,
) -> None:
    from cteforge.services.describe import get_definition

    ctx.store.add_user(AGENT_ADMIN)
    _strict(ctx)
    pending = _propose(ctx)  # alice's
    out = review.approve(ctx, AGENT_ADMIN, pending.proposal.id, verify=True)
    assert out.definition.state is DefinitionState.VERIFIED
    assert out.proposal.decided_by == "botadmin"
    assert out.proposal.decided_by_kind is ActorKind.AGENT
    card = get_definition(ctx, ALICE, "realization_rate")
    assert card.verified_by == "botadmin" and card.verified_by_kind is ActorKind.AGENT
    # A change to the now verified definition: an admin agent may approve it, an
    # approver agent may not.
    change = revise_definition(
        ctx, ALICE, id="realization_rate", base_version=1, metadata={"keywords": ["z"]}
    )
    with pytest.raises(PermissionDeniedError):
        review.approve(ctx, AGENT_APPROVER, change.proposal.id)
    assert review.approve(ctx, AGENT_ADMIN, change.proposal.id).definition.version == 2
    review.verify_definition(ctx, AGENT_ADMIN, "realization_rate", 2)


def test_a_human_verifier_kind_is_shown(ctx: AppContext) -> None:
    from cteforge.services.describe import get_definition

    ctx.store.add_user(APPROVER)
    _propose(ctx)
    review.verify_definition(ctx, APPROVER, "realization_rate", 1)
    card = get_definition(ctx, ALICE, "realization_rate")
    assert card.verified_by == "ann" and card.verified_by_kind is ActorKind.HUMAN


def test_custom_role_without_review_all_approves_only_what_it_owns(ctx: AppContext) -> None:
    from cteforge.domain.access import Tool

    tools = frozenset({Tool.REVIEW_PROPOSAL, Tool.PROPOSE_JOIN})
    owner = User(name="alice", role="triage", tools=tools, review_all=False)
    _propose(ctx)  # alice owns realization_rate
    _strict(ctx)
    change = revise_definition(
        ctx, BOT, id="realization_rate", base_version=1, metadata={"keywords": ["z"]}
    )
    new = _propose(ctx, BOT, id="other_def", owner=None)
    with pytest.raises(PermissionDeniedError):
        review.approve(ctx, owner, new.proposal.id)  # not hers
    assert review.approve(ctx, owner, change.proposal.id).definition.version == 2
