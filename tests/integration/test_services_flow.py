"""The whole product flow through services only (DESIGN §6-§11, §17-§18).

SQLite library + a connections.yml with one DuckDB profile on a tmp copy of the demo
warehouse (seeded by examples/seed_warehouse.py; examples/warehouse.duckdb is gitignored).
An agent builds the library, a person verifies it, the team goes strict, and the audit log
and the YAML export/import are checked at the end.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from cteforge.domain.audit import AuditKind, AuditOutcome
from cteforge.domain.errors import PermissionDeniedError
from cteforge.domain.issues import IssueCode
from cteforge.domain.models import DefinitionState, TeamMode, TrustLabel
from cteforge.domain.proposals import ProposalKind, ProposalStatus
from cteforge.domain.users import ActorKind, User, UserRole
from cteforge.services import (
    admin,
    audit_log,
    describe,
    library_io,
    propose,
    propose_other,
    review,
    run,
    search,
)
from cteforge.services import parse_query as parse_query_uc
from cteforge.services.context import AppConfig, AppContext, open_context

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"
SQL = (EXAMPLES / "queries" / "realization_rate.sql").read_text()
ANNOTATIONS = yaml.safe_load(
    (EXAMPLES / "definitions" / "realization_rate.annotations.yml").read_text()
)
METADATA = {k: v for k, v in ANNOTATIONS.items() if k != "variables"}
RR = "realization_rate"
Q3_APAC = {"period": {"start": "2025-07-01", "end": "2025-10-01"}, "regions": ["APAC"]}
RAW_JOIN_SQL = (
    "SELECT a.region, COUNT(*) AS deals FROM deals AS d "
    "JOIN accounts AS a ON a.account_id = d.account_id GROUP BY a.region ORDER BY a.region"
)


@pytest.fixture
def workdir(tmp_path: Path, warehouse_path: Path) -> Path:
    shutil.copy(warehouse_path, tmp_path / "warehouse.duckdb")
    (tmp_path / "connections.yml").write_text(
        "profiles:\n"
        "  demo:\n"
        "    dialect: duckdb\n"
        "    options: {path: warehouse.duckdb}\n"
        "    max_rows: 1000\n"
    )
    return tmp_path


def _open(workdir: Path, db: str) -> AppContext:
    return open_context(
        AppConfig(
            db_url=f"sqlite:///{workdir / db}",
            connections_path=workdir / "connections.yml",
            default_profile="demo",
        )
    )


def _team(ctx: AppContext) -> tuple[User, User, User]:
    """(admin, agent contributor, human approver), created through the admin use-cases."""
    root, root_token = admin.bootstrap_admin(ctx, "root")
    agent, agent_token = admin.create_user(
        ctx, root, "claude-agent", UserRole.CONTRIBUTOR, ActorKind.AGENT
    )
    ana, _ = admin.create_user(ctx, root, "ana", UserRole.APPROVER, ActorKind.HUMAN)
    assert root.role == UserRole.ADMIN and agent.kind is ActorKind.AGENT
    assert root_token != agent_token and len(agent_token) >= 40
    return root, agent, ana


def _run_rr(ctx: AppContext, user: User, version: int | None = None):
    ref: dict[str, Any] = {"id": RR} if version is None else {"id": RR, "version": version}
    return run.compile_and_run(
        ctx, user, uses={"rr": ref}, values={"rr": Q3_APAC}, question="Q3 2025 APAC realization?"
    )


def _no_sql_text(event_json: str) -> None:
    lowered = event_json.lower()
    for fragment in ("select ", "closed_deals", " from ", "join accounts", "paid_amount"):
        assert fragment not in lowered, f"audit event contains SQL text ({fragment!r})"


@pytest.mark.usefixtures("mcp_surface")  # reads are logged through MCP (ADR 0029)
def test_whole_flow_through_services(workdir: Path) -> None:
    with _open(workdir, "library.db") as ctx:
        root, agent, ana = _team(ctx)

        # 0. Exploratory SQL before the library exists: the deals↔accounts join is unknown.
        exploratory = run.check_and_run(ctx, agent, RAW_JOIN_SQL)
        assert exploratory.ran and exploratory.label is TrustLabel.EXPLORATORY
        assert {i.code for i in exploratory.issues} == {IssueCode.UNKNOWN_JOIN}
        assert [r[0] for r in exploratory.rows] == ["APAC", "EMEA", "LATAM", "NA"]

        # 1. The agent parses the trusted query: literal IDs, with column types from the profile.
        report = parse_query_uc.parse_query(ctx, agent, SQL, "duckdb")
        literal_ids = {lit["id"] for lit in report.literals}
        assert {"closed_deals.L1", "closed_deals.L2", "deals_in_region.L1"} <= literal_ids
        assert report.schema_found and report.issues == []

        # 2. It proposes the definition with the example annotations: checks pass on the
        #    warehouse, so in open mode it is applied at once as llm_annotated.
        proposed = propose.propose_definition(
            ctx,
            agent,
            sql=SQL,
            dialect="duckdb",
            metadata=METADATA,
            annotations=ANNOTATIONS["variables"],
            summary="Realization rate from the finance team's query",
        )
        assert proposed.auto_applied and proposed.issues == []
        assert proposed.proposal.status is ProposalStatus.APPROVED
        assert proposed.definition is not None
        assert proposed.definition.state is DefinitionState.LLM_ANNOTATED
        assert proposed.definition.version == 1 and proposed.definition.profile == "demo"
        assert proposed.definition.owner is None  # An agent's definition has no owner.

        # 3. Search finds it.
        hits = search.search(ctx, agent, "realization rate")
        assert [h.id for h in hits] == [RR] and hits[0].score > 0
        assert hits[0].state is DefinitionState.LLM_ANNOTATED

        # 4. Run by reference with Q3 2025 / APAC: 3 months, labeled llm_annotated.
        first = _run_rr(ctx, agent)
        assert first.ran and first.usable and first.label is TrustLabel.LLM_ANNOTATED
        assert first.row_count == 3 and first.columns == ["month", "region", "realization_rate"]
        assert [row[0][:7] for row in first.rows] == ["2025-07", "2025-08", "2025-09"]
        assert {row[1] for row in first.rows} == {"APAC"}
        assert first.definitions == [f"{RR}@1"]
        assert f"compile_id={first.compile_id}" in first.sql  # ADR 0008 tag.

        # 5. Agents can never verify; a human approver does.
        with pytest.raises(PermissionDeniedError):
            review.verify_definition(ctx, agent, RR, 1)
        card = review.verify_definition(ctx, ana, RR, 1)
        assert card.state is DefinitionState.VERIFIED
        assert card.verified_by == "ana" and card.verified_at == ctx.now().date()
        verified_run = _run_rr(ctx, agent)
        assert verified_run.label is TrustLabel.VERIFIED and verified_run.row_count == 3
        assert verified_run.rows == first.rows

        # 6. The agent revises the (now verified) metadata: never auto-applied (ADR 0007).
        revision = propose.revise_definition(
            ctx,
            agent,
            id=RR,
            base_version=1,
            metadata={"keywords": [*METADATA["keywords"], "cash conversion"]},
            summary="More keywords",
        )
        assert not revision.auto_applied and revision.definition is None
        assert revision.proposal.status is ProposalStatus.PENDING
        assert revision.proposal.kind is ProposalKind.REVISE_DEFINITION
        assert "cash conversion" in revision.proposal.diff
        pending = review.list_proposals(ctx, ana, ProposalStatus.PENDING)
        assert [p.id for p in pending] == [revision.proposal.id]
        # The human approves without verifying: v2 is llm_annotated, flagged non-logic.
        approved = review.approve(ctx, ana, revision.proposal.id, comment="ok")
        assert approved.definition is not None and approved.definition.version == 2
        assert approved.definition.state is DefinitionState.LLM_ANNOTATED
        assert approved.proposal.payload["logic_changed"] is False
        history = describe.definition_history(ctx, agent, RR)
        assert [(h.version, h.state) for h in history] == [
            (1, DefinitionState.VERIFIED),
            (2, DefinitionState.LLM_ANNOTATED),
        ]
        assert search.search(ctx, agent, "cash conversion")[0].version == 2

        # 7. The deals↔accounts join was mined from realization_rate's SQL (through its
        #    closed_deals CTE, whose account_id passes through from deals), so the raw SQL
        #    that was exploratory before the library existed now runs on a known join.
        known = run.check_and_run(ctx, agent, RAW_JOIN_SQL)
        assert known.label is TrustLabel.NEW_LOGIC_KNOWN_JOINS and known.issues == []
        assert known.rows == exploratory.rows
        # The agent refines the mined edge (relationship); auto-applied in open mode.
        join = propose_other.propose_join(
            ctx,
            agent,
            left_table="deals",
            right_table="accounts",
            keys=[("account_id", "account_id")],
            relationship="many_to_one",
        )
        assert (
            join.auto_applied
            and join.proposal.target_id == "accounts__deals__account_id=account_id"
        )
        edge = ctx.store.get_join(join.proposal.target_id)
        # Stored canonically (tables sorted), so deals→accounts many_to_one reads reversed.
        assert edge is not None
        assert (edge.left_table, edge.right_table) == ("accounts", "deals")
        assert edge.relationship == "one_to_many"

        # 8. Strict mode: floating runs fall back to verified v1; llm_annotated v2 and raw
        #    SQL are refused and nothing runs.
        assert admin.set_settings(ctx, root, TeamMode.STRICT).mode is TeamMode.STRICT
        floating = _run_rr(ctx, agent)
        assert floating.ran and floating.label is TrustLabel.VERIFIED
        assert floating.definitions == [f"{RR}@1"]
        pinned = _run_rr(ctx, agent, version=2)
        assert not pinned.ran and not pinned.usable and pinned.rows == []
        assert IssueCode.STRICT_MODE_BLOCKED in {i.code for i in pinned.issues}
        assert pinned.label is TrustLabel.LLM_ANNOTATED and pinned.compile_id is None
        blocked_raw = run.check_and_run(ctx, agent, RAW_JOIN_SQL)
        assert not blocked_raw.ran
        assert IssueCode.STRICT_MODE_BLOCKED in {i.code for i in blocked_raw.issues}
        # In strict mode search shows only the verified version.
        assert [(h.id, h.version) for h in search.search(ctx, agent, "realization")] == [(RR, 1)]

        # 9. Audit: agents can't read it; the admin sees every call, and no SQL text.
        with pytest.raises(PermissionDeniedError):
            audit_log.list_audit(ctx, agent)
        page = audit_log.list_audit(ctx, root, limit=1000)
        assert not page.has_more
        events = list(reversed(page.events))  # oldest first
        kinds = [e.kind for e in events]
        for kind in (
            AuditKind.ADMIN,
            AuditKind.PARSE_QUERY,
            AuditKind.PROPOSE,
            AuditKind.SEARCH,
            AuditKind.COMPILE_AND_RUN,
            AuditKind.REVIEW,
            AuditKind.GET_DEFINITION,
            AuditKind.CHECK_AND_RUN,
        ):
            assert kind in kinds, kind
        for event in events:
            _no_sql_text(event.model_dump_json())

        parse_event = next(e for e in events if e.kind is AuditKind.PARSE_QUERY)
        assert parse_event.sql_sha256 == hashlib.sha256(SQL.encode()).hexdigest()

        runs = [e for e in events if e.kind is AuditKind.COMPILE_AND_RUN]
        assert [(e.outcome, e.trust_label, e.definitions) for e in runs] == [
            (AuditOutcome.OK, TrustLabel.LLM_ANNOTATED, [f"{RR}@1"]),
            (AuditOutcome.OK, TrustLabel.VERIFIED, [f"{RR}@1"]),
            (AuditOutcome.OK, TrustLabel.VERIFIED, [f"{RR}@1"]),
            (AuditOutcome.REJECTED, TrustLabel.LLM_ANNOTATED, [f"{RR}@2"]),
        ]
        assert runs[0].compile_id == first.compile_id and runs[0].row_count == 3
        assert runs[0].question == "Q3 2025 APAC realization?"
        assert runs[0].values["rr"]["regions"] == ["APAC"]
        assert runs[0].values["rr"]["period"] == Q3_APAC["period"]
        assert runs[0].sql_sha256 == hashlib.sha256(first.sql.encode()).hexdigest()
        assert all(e.actor == "claude-agent" and e.actor_kind is ActorKind.AGENT for e in runs)

        checks = [e for e in events if e.kind is AuditKind.CHECK_AND_RUN]
        assert [(e.outcome, e.trust_label) for e in checks] == [
            (AuditOutcome.OK, TrustLabel.EXPLORATORY),
            (AuditOutcome.OK, TrustLabel.NEW_LOGIC_KNOWN_JOINS),
            (AuditOutcome.REJECTED, TrustLabel.NEW_LOGIC_KNOWN_JOINS),
        ]
        reviews = [
            (e.actor, e.outcome, e.extra.get("op")) for e in events if e.kind is AuditKind.REVIEW
        ]
        assert ("claude-agent", AuditOutcome.REJECTED, "verify_definition") in reviews
        assert ("ana", AuditOutcome.OK, "verify_definition") in reviews
        assert ("ana", AuditOutcome.OK, "approve") in reviews
        refused_listing = [
            e for e in events if e.kind is AuditKind.ADMIN and e.extra.get("op") == "list_audit"
        ]
        assert [(e.actor, e.outcome) for e in refused_listing] == [
            ("claude-agent", AuditOutcome.REJECTED)
        ]
        assert json.loads(page.model_dump_json())["limit"] == 1000

        # 10. Export → import (unchanged: no proposals) → export gives identical files.
        first_dir, second_dir = workdir / "export1", workdir / "export2"
        written = library_io.export_library(ctx, root, first_dir)
        assert sorted(Path(p).relative_to(first_dir).as_posix() for p in written) == [
            f"definitions/{RR}.yml",
            "joins/accounts__deals__account_id=account_id.yml",
            "joins/deals__invoices__deal_id=deal_id.yml",  # Mined through `collected`.
        ]
        assert library_io.import_library(ctx, agent, first_dir) == []
        library_io.export_library(ctx, root, second_dir)
        assert _files(first_dir) == _files(second_dir)
        exported = yaml.safe_load((first_dir / "definitions" / f"{RR}.yml").read_text())
        assert exported["version"] == 2 and "cash conversion" in exported["keywords"]


def test_library_files_rebuild_a_fresh_library(workdir: Path) -> None:
    """Export a library, import it into an empty one: every file becomes a proposal that is
    re-checked on the warehouse and auto-applied (open mode), runs give the same rows, and
    exporting the new library gives the same files."""
    with _open(workdir, "source.db") as ctx:
        root, agent, _ = _team(ctx)
        propose.propose_definition(
            ctx,
            agent,
            sql=SQL,
            dialect="duckdb",
            metadata=METADATA,
            annotations=ANNOTATIONS["variables"],
        )
        composed = propose.propose_definition(
            ctx,
            agent,
            composition={"uses": {"rr": {"id": RR}}, "fixed": {"rr": Q3_APAC}},
            dialect="duckdb",
            metadata={"id": "apac_q3_realization", "title": "APAC realization, Q3 2025"},
        )
        assert composed.auto_applied and composed.issues == []
        uses = {"q": {"id": "apac_q3_realization"}}
        expected = run.compile_and_run(ctx, agent, uses=uses)
        assert expected.row_count == 3
        library_io.export_library(ctx, root, workdir / "out")

    with _open(workdir, "fresh.db") as fresh:
        root, agent, _ = _team(fresh)
        outcomes = library_io.import_library(fresh, agent, workdir / "out")
        assert [(o.proposal.kind, o.auto_applied, o.issues) for o in outcomes] == [
            (ProposalKind.NEW_DEFINITION, True, []),
            (ProposalKind.NEW_DEFINITION, True, []),  # the composed one
        ]
        assert outcomes[0].definition is not None
        assert outcomes[0].definition.state is DefinitionState.LLM_ANNOTATED
        rerun = run.compile_and_run(fresh, agent, uses=uses)
        assert rerun.ran and rerun.label is TrustLabel.LLM_ANNOTATED
        assert rerun.rows == expected.rows
        library_io.export_library(fresh, root, workdir / "again")
    assert _files(workdir / "out") == _files(workdir / "again")


def _files(directory: Path) -> dict[str, str]:
    return {
        p.relative_to(directory).as_posix(): p.read_text() for p in sorted(directory.rglob("*.yml"))
    }
