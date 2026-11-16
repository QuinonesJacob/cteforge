"""compile_and_run end to end: SQLite store + DuckDB profile on a copy of the demo warehouse."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from cteforge.domain.audit import AuditKind, AuditOutcome
from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import NotFoundError, PolicyError, RenderError, VariableValueError
from cteforge.domain.issues import IssueCode, Severity
from cteforge.domain.models import (
    Definition,
    DefinitionRef,
    DefinitionState,
    JoinEdge,
    JoinKey,
    Recipe,
    TeamMode,
    TrustLabel,
)
from cteforge.domain.users import ActorKind, User, UserRole
from cteforge.files.definitions import load_definition
from cteforge.services._library import matching_edge
from cteforge.services.cards import json_rows
from cteforge.services.context import AppConfig, AppContext, open_context, set_team_mode
from cteforge.services.run import compile_and_run

ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = ROOT / "examples"
AGENT = User(name="claude-agent", role=UserRole.VIEWER, kind=ActorKind.AGENT)

Q3_APAC = {"period": {"start": "2025-07-01", "end": "2025-10-01"}, "regions": ["APAC"]}


@pytest.fixture(scope="module")
def wh_file(warehouse_path: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("run_wh") / "warehouse.duckdb"
    shutil.copy(warehouse_path, path)
    return path


def _ctx(tmp_path: Path, wh_file: Path, *, max_rows: int = 10_000) -> AppContext:
    profile = WarehouseProfile(
        name="demo", dialect=Dialect.DUCKDB, options={"path": str(wh_file)}, max_rows=max_rows
    )
    return open_context(AppConfig(db_url=f"sqlite:///{tmp_path / 'lib.db'}"), profiles=[profile])


@pytest.fixture
def ctx(tmp_path: Path, wh_file: Path):
    with _ctx(tmp_path, wh_file) as context:
        yield context


def example(name: str, **update) -> Definition:
    return load_definition(EXAMPLES / "definitions" / f"{name}.yml").model_copy(update=update)


def add(ctx: AppContext, definition: Definition) -> Definition:
    return ctx.store.add_definition_version(definition, author="analyst")


def rr_verified(**update) -> Definition:
    return example("realization_rate", state=DefinitionState.VERIFIED, **update)


def original_sql(name: str) -> str:
    return (EXAMPLES / "queries" / f"{name}.sql").read_text()


def last_event(ctx: AppContext):
    return ctx.audit.list_events(limit=1)[0]


# ── single definition ────────────────────────────────────────────────────────────────


def test_single_definition_returns_the_original_querys_rows(ctx: AppContext) -> None:
    add(ctx, rr_verified())
    out = compile_and_run(ctx, AGENT, uses={"rr": {"id": "realization_rate"}}, limit=1000)
    expected = ctx.warehouses.get("demo").run(original_sql("realization_rate"), 1000)
    assert out.ran and out.usable
    assert out.label is TrustLabel.VERIFIED
    assert out.columns == expected.columns == ["month", "region", "realization_rate"]
    assert out.rows == json_rows(expected.rows) and out.row_count == len(expected.rows) > 0
    assert out.definitions == ["realization_rate@1"]
    assert out.sql.startswith(f"/* cteforge: realization_rate v1 compile_id={out.compile_id} */\n")
    assert not [i for i in out.issues if i.severity is Severity.ERROR]


def test_values_q3_2025_apac(ctx: AppContext) -> None:
    add(ctx, rr_verified())
    out = compile_and_run(
        ctx, AGENT, uses={"rr": {"id": "realization_rate"}}, values={"rr": Q3_APAC}
    )
    expected_sql = (
        original_sql("realization_rate")
        .replace("TIMESTAMP '2026-01-01'", "TIMESTAMP '2025-07-01'")
        .replace("TIMESTAMP '2026-04-01'", "TIMESTAMP '2025-10-01'")
        .replace("IN ('EMEA', 'NA')", "IN ('APAC')")
    )
    expected = ctx.warehouses.get("demo").run(expected_sql, 1000)
    assert out.rows == json_rows(expected.rows)
    assert {row[1] for row in out.rows} == {"APAC"}
    assert {row[0][:7] for row in out.rows} == {"2025-07", "2025-08", "2025-09"}
    assert out.label is TrustLabel.VERIFIED  # changeable variables, allowed value
    event = last_event(ctx)
    assert event.values == {
        "rr": {
            "period": {"start": "2025-07-01", "end": "2025-10-01"},
            "won_statuses": ["closed_won", "closed_won_renewal"],
            "regions": ["APAC"],
            "paid_status": "paid",
        }
    }


def test_definition_ref_and_plain_id_are_accepted(ctx: AppContext) -> None:
    add(ctx, rr_verified())
    a = compile_and_run(ctx, AGENT, uses={"rr": "realization_rate"}, run=False)
    b = compile_and_run(
        ctx, AGENT, uses={"rr": DefinitionRef(id="realization_rate", version=1)}, run=False
    )
    assert a.definitions == b.definitions == ["realization_rate@1"]


# ── final SELECT over several definitions ────────────────────────────────────────────


def test_same_definition_twice_with_different_values(ctx: AppContext) -> None:
    add(ctx, rr_verified())
    out = compile_and_run(
        ctx,
        AGENT,
        uses={"q3": {"id": "realization_rate"}, "q1": {"id": "realization_rate"}},
        values={"q3": Q3_APAC},
        select="SELECT 'q3' AS q, region, realization_rate FROM q3 "
        "UNION ALL SELECT 'q1', region, realization_rate FROM q1",
    )
    q3 = compile_and_run(ctx, AGENT, uses={"x": {"id": "realization_rate"}}, values={"x": Q3_APAC})
    q1 = compile_and_run(ctx, AGENT, uses={"x": {"id": "realization_rate"}})
    assert out.ran and out.label is TrustLabel.BUILT_FROM_VERIFIED
    assert out.row_count == q3.row_count + q1.row_count
    assert {r[1] for r in out.rows if r[0] == "q3"} == {"APAC"}
    assert {r[1] for r in out.rows if r[0] == "q1"} == {"EMEA", "NA"}
    assert out.definitions == ["realization_rate@1"]  # de-duplicated in tag and audit


def test_join_on_declared_keys_is_built_from_verified_parts(ctx: AppContext) -> None:
    add(ctx, rr_verified(join_keys=["region"]))
    add(ctx, example("booked_vs_collected", state=DefinitionState.VERIFIED, join_keys=["region"]))
    out = compile_and_run(
        ctx,
        AGENT,
        uses={"rr": {"id": "realization_rate"}, "bc": {"id": "booked_vs_collected"}},
        select="SELECT rr.month, rr.region, rr.realization_rate, bc.booked "
        "FROM rr JOIN bc ON rr.region = bc.region",
    )
    assert out.ran and out.row_count > 0
    assert out.label is TrustLabel.BUILT_FROM_VERIFIED
    codes = {i.code for i in out.issues}
    assert IssueCode.FAN_OUT_RISK in codes and IssueCode.UNKNOWN_JOIN not in codes
    assert out.definitions == ["realization_rate@1", "booked_vs_collected@1"]
    assert out.sql.startswith("/* cteforge: realization_rate v1, booked_vs_collected v1 ")


def test_unknown_join_between_definitions_lowers_label(ctx: AppContext) -> None:
    add(ctx, rr_verified())  # no join_keys declared
    add(ctx, example("booked_vs_collected", state=DefinitionState.VERIFIED))
    out = compile_and_run(
        ctx,
        AGENT,
        uses={"rr": {"id": "realization_rate"}, "bc": {"id": "booked_vs_collected"}},
        select="SELECT rr.region, bc.booked FROM rr JOIN bc ON rr.region = bc.region",
    )
    assert out.ran  # open mode: guide, don't restrict
    assert out.label is TrustLabel.EXPLORATORY
    assert IssueCode.UNKNOWN_JOIN in {i.code for i in out.issues}


def test_join_to_raw_table_is_unknown(ctx: AppContext) -> None:
    add(ctx, rr_verified())
    out = compile_and_run(
        ctx,
        AGENT,
        uses={"rr": {"id": "realization_rate"}},
        select="SELECT rr.region, a.name FROM rr JOIN accounts AS a ON a.region = rr.region",
        run=False,
    )
    assert out.label is TrustLabel.EXPLORATORY
    assert any(i.code is IssueCode.UNKNOWN_JOIN and "accounts" in i.message for i in out.issues)


def test_raw_tables_joined_by_a_known_join_map_edge(ctx: AppContext) -> None:
    add(ctx, rr_verified())
    select = (
        "SELECT rr.region, rr.realization_rate FROM rr WHERE rr.region IN ("
        "SELECT a.region FROM deals AS d JOIN accounts AS a ON a.account_id = d.account_id)"
    )
    uses = {"rr": {"id": "realization_rate"}}
    before = compile_and_run(ctx, AGENT, uses=uses, select=select, run=False)
    assert before.label is TrustLabel.EXPLORATORY
    ctx.store.upsert_join(
        JoinEdge(
            id="accounts__deals__account_id",
            left_table="accounts",
            right_table="deals",
            keys=[JoinKey(left="account_id", right="account_id")],
        )
    )
    after = compile_and_run(ctx, AGENT, uses=uses, select=select)
    # Known join, but raw tables are read: new logic, never "built from verified parts".
    assert after.ran and after.label is TrustLabel.NEW_LOGIC_KNOWN_JOINS
    assert IssueCode.UNKNOWN_JOIN not in {i.code for i in after.issues}
    assert any("raw table" in r for r in after.reasons)


def test_raw_tables_on_a_known_join_are_refused_in_strict_mode(ctx: AppContext) -> None:
    """Regression: a final SELECT that ignores the aliases and reads raw tables joined on a
    join-map edge was labeled built_from_verified_parts and ran in strict mode."""
    add(ctx, rr_verified())
    ctx.store.upsert_join(
        JoinEdge(
            id="accounts__deals__account_id",
            left_table="accounts",
            right_table="deals",
            keys=[JoinKey(left="account_id", right="account_id")],
        )
    )
    set_team_mode(ctx, TeamMode.STRICT)
    out = compile_and_run(
        ctx,
        AGENT,
        uses={"rr": {"id": "realization_rate"}},
        select=(
            "SELECT a.region, SUM(d.amount) FROM deals d JOIN accounts a "
            "ON d.account_id = a.account_id GROUP BY 1"
        ),
    )
    assert out.label is TrustLabel.NEW_LOGIC_KNOWN_JOINS
    assert not out.usable and not out.ran and out.rows == []
    assert IssueCode.STRICT_MODE_BLOCKED in {i.code for i in out.issues}


def test_outcome_carries_verifier_and_date(ctx: AppContext) -> None:
    """Regression: a verified result must be labeled with verifier + date (DESIGN §8)."""
    add(ctx, rr_verified(verified_by="ann", verified_at=dt.date(2026, 11, 1)))
    out = compile_and_run(
        ctx, AGENT, uses={"rr": {"id": "realization_rate"}}, values={"rr": Q3_APAC}
    )
    assert out.label is TrustLabel.VERIFIED
    dumped = out.model_dump(mode="json")["used"]
    assert dumped == [
        {
            "id": "realization_rate",
            "version": 1,
            "state": "verified",
            "verified_by": "ann",
            "verified_at": "2026-11-01",
        }
    ]


def test_matching_edge_orientation_and_keys() -> None:
    edge = JoinEdge(
        id="e",
        left_table="analytics.deals",
        right_table="accounts",
        keys=[JoinKey(left="account_id", right="id")],
    )
    assert matching_edge("deals", "ACCOUNTS", [("ACCOUNT_ID", "id")], [edge]) is edge
    assert matching_edge("accounts", "deals", [("id", "account_id")], [edge]) is edge
    assert matching_edge("accounts", "deals", [("account_id", "id")], [edge]) is None
    assert matching_edge("other.deals", "accounts", [("account_id", "id")], [edge]) is None
    assert matching_edge("deals", "accounts", [], [edge]) is None


def test_not_read_only_final_select_is_refused(ctx: AppContext) -> None:
    add(ctx, rr_verified())
    out = compile_and_run(
        ctx,
        AGENT,
        uses={"rr": {"id": "realization_rate"}},
        select="SELECT * FROM rr FOR UPDATE",
    )
    assert not out.ran and not out.usable and out.sql == ""
    assert IssueCode.NOT_READ_ONLY in {i.code for i in out.issues}
    assert last_event(ctx).outcome is AuditOutcome.REJECTED


# ── trust policy ─────────────────────────────────────────────────────────────────────


def test_llm_annotated_is_labeled_in_open_mode_and_blocked_in_strict(ctx: AppContext) -> None:
    add(ctx, example("realization_rate"))  # llm_annotated
    uses = {"rr": {"id": "realization_rate"}}
    open_out = compile_and_run(ctx, AGENT, uses=uses)
    assert open_out.ran and open_out.label is TrustLabel.LLM_ANNOTATED

    set_team_mode(ctx, TeamMode.STRICT)
    strict = compile_and_run(ctx, AGENT, uses=uses)
    assert not strict.ran and not strict.usable and strict.rows == []
    assert IssueCode.STRICT_MODE_BLOCKED in {i.code for i in strict.issues}
    assert strict.compile_id is None
    event = last_event(ctx)
    assert event.outcome is AuditOutcome.REJECTED
    assert event.trust_label is TrustLabel.LLM_ANNOTATED


def test_strict_mode_floats_to_newest_verified_version(ctx: AppContext) -> None:
    add(ctx, rr_verified())
    add(ctx, example("realization_rate", version=2))  # llm_annotated v2
    set_team_mode(ctx, TeamMode.STRICT)
    out = compile_and_run(ctx, AGENT, uses={"rr": {"id": "realization_rate"}})
    assert out.ran and out.definitions == ["realization_rate@1"]
    assert out.label is TrustLabel.VERIFIED


def test_draft_is_refused(ctx: AppContext) -> None:
    add(ctx, example("realization_rate", state=DefinitionState.DRAFT))
    out = compile_and_run(ctx, AGENT, uses={"rr": {"id": "realization_rate"}})
    assert not out.ran and not out.usable
    assert IssueCode.DRAFT_NOT_USABLE in {i.code for i in out.issues}
    assert last_event(ctx).outcome is AuditOutcome.REJECTED


def test_floating_skips_a_newer_draft_but_a_pinned_draft_is_refused(ctx: AppContext) -> None:
    add(ctx, rr_verified())
    add(ctx, example("realization_rate", version=2, state=DefinitionState.DRAFT))
    floating = compile_and_run(ctx, AGENT, uses={"rr": {"id": "realization_rate"}})
    assert floating.ran and floating.definitions == ["realization_rate@1"]
    pinned = compile_and_run(ctx, AGENT, uses={"rr": {"id": "realization_rate", "version": 2}})
    assert not pinned.ran and IssueCode.DRAFT_NOT_USABLE in {i.code for i in pinned.issues}


def test_fixed_override_warns_and_lowers_label(ctx: AppContext) -> None:
    add(ctx, rr_verified())
    uses = {"rr": {"id": "realization_rate"}}
    out = compile_and_run(ctx, AGENT, uses=uses, values={"rr": {"won_statuses": ["closed_won"]}})
    assert out.ran
    assert out.label is TrustLabel.BUILT_FROM_VERIFIED  # verified, one step down
    override = [i for i in out.issues if i.code is IssueCode.FIXED_OVERRIDE]
    assert override and override[0].variable == "won_statuses"
    assert any("overridden" in r for r in out.reasons)

    set_team_mode(ctx, TeamMode.STRICT)
    strict = compile_and_run(ctx, AGENT, uses=uses, values={"rr": {"won_statuses": ["closed_won"]}})
    assert not strict.ran and IssueCode.STRICT_MODE_BLOCKED in {i.code for i in strict.issues}


def test_value_outside_allowed_lowers_label(ctx: AppContext) -> None:
    add(ctx, rr_verified())
    out = compile_and_run(
        ctx, AGENT, uses={"rr": {"id": "realization_rate"}}, values={"rr": {"regions": ["MARS"]}}
    )
    assert out.ran and out.row_count == 0
    assert out.label is TrustLabel.BUILT_FROM_VERIFIED
    assert IssueCode.VALUE_NOT_ALLOWED in {i.code for i in out.issues}


# ── limits, run=False, failures ──────────────────────────────────────────────────────


def test_row_limit_truncates_and_warns(ctx: AppContext) -> None:
    add(ctx, rr_verified())
    out = compile_and_run(ctx, AGENT, uses={"rr": {"id": "realization_rate"}}, limit=2)
    assert out.row_count == 2 and len(out.rows) == 2 and out.truncated
    assert IssueCode.ROW_LIMIT_REACHED in {i.code for i in out.issues}
    assert last_event(ctx).row_count == 2


def test_limit_is_capped_by_profile_max_rows(tmp_path: Path, wh_file: Path) -> None:
    with _ctx(tmp_path, wh_file, max_rows=3) as small:
        add(small, rr_verified())
        out = compile_and_run(small, AGENT, uses={"rr": {"id": "realization_rate"}}, limit=100)
        assert out.row_count == 3 and out.truncated


def test_run_false_compiles_and_explains_only(ctx: AppContext) -> None:
    add(ctx, rr_verified())
    out = compile_and_run(ctx, AGENT, uses={"rr": {"id": "realization_rate"}}, run=False)
    assert not out.ran and out.usable and out.rows == [] and out.compile_id
    event = last_event(ctx)
    assert event.outcome is AuditOutcome.OK and event.row_count is None
    assert event.compile_id == out.compile_id


def test_run_false_without_any_warehouse_compiles_without_explain(tmp_path: Path) -> None:
    with open_context(AppConfig(db_url=f"sqlite:///{tmp_path / 'x.db'}"), profiles=[]) as bare:
        add(bare, rr_verified())
        out = compile_and_run(bare, AGENT, uses={"rr": {"id": "realization_rate"}}, run=False)
        assert out.dialect is Dialect.DUCKDB and not out.ran
        assert IssueCode.CHECKS_INCOMPLETE in {i.code for i in out.issues}
        with pytest.raises(PolicyError):
            compile_and_run(bare, AGENT, uses={"rr": {"id": "realization_rate"}})


def test_run_false_compiles_for_another_dialect_without_explain(ctx: AppContext) -> None:
    # DESIGN §12: rendering for another dialect is allowed, labeled and lowers trust.
    add(ctx, rr_verified())
    uses = {"rr": {"id": "realization_rate"}}
    out = compile_and_run(
        ctx, AGENT, uses=uses, values={"rr": Q3_APAC}, dialect=Dialect.SNOWFLAKE, run=False
    )
    assert out.dialect is Dialect.SNOWFLAKE and out.usable and not out.ran
    codes = {i.code for i in out.issues}
    assert {IssueCode.TRANSPILED, IssueCode.CHECKS_INCOMPLETE} <= codes
    assert out.label is not TrustLabel.VERIFIED
    assert last_event(ctx).outcome is AuditOutcome.OK
    with pytest.raises(PolicyError, match="run=false"):
        compile_and_run(ctx, AGENT, uses=uses, dialect=Dialect.SNOWFLAKE, run=True)


def test_warehouse_error_returns_run_failed_and_audits_class_only(ctx: AppContext) -> None:
    add(ctx, rr_verified())
    out = compile_and_run(
        ctx,
        AGENT,
        uses={"rr": {"id": "realization_rate"}},
        values={"rr": Q3_APAC},
        select="SELECT rr.no_such_column FROM rr",
    )
    assert not out.ran and out.rows == []
    assert out.usable is False  # no answer, not an empty one
    failed = [i for i in out.issues if i.code is IssueCode.RUN_FAILED]
    assert failed and failed[0].severity is Severity.ERROR
    assert "no_such_column" in failed[0].message  # the caller can fix its SELECT
    event = last_event(ctx)
    assert event.outcome is AuditOutcome.ERROR and event.error == "WarehouseError"
    dumped = event.model_dump_json()
    assert "no_such_column" not in dumped and "SELECT" not in dumped.upper()


def test_audit_event_has_metadata_but_no_sql(ctx: AppContext) -> None:
    add(ctx, rr_verified())
    out = compile_and_run(
        ctx,
        AGENT,
        uses={"rr": {"id": "realization_rate"}},
        values={"rr": Q3_APAC},
        question="Realization in APAC for Q3 2025?",
    )
    event = last_event(ctx)
    assert event.kind is AuditKind.COMPILE_AND_RUN and event.outcome is AuditOutcome.OK
    assert event.actor == "claude-agent" and event.actor_kind is ActorKind.AGENT
    assert event.definitions == ["realization_rate@1"]
    assert event.trust_label is TrustLabel.VERIFIED
    assert event.compile_id == out.compile_id
    assert event.sql_sha256 == hashlib.sha256(out.sql.encode()).hexdigest()
    assert event.row_count == out.row_count and event.duration_ms is not None
    assert event.question == "Realization in APAC for Q3 2025?"
    text = json.dumps(event.model_dump(mode="json")).upper()
    assert "SELECT" not in text and "FROM DEALS" not in text and "CTEFORGE:" not in text


# ── input errors ─────────────────────────────────────────────────────────────────────


def test_unknown_definition_raises_and_is_audited(ctx: AppContext) -> None:
    with pytest.raises(NotFoundError):
        compile_and_run(ctx, AGENT, uses={"x": {"id": "nope"}})
    event = last_event(ctx)
    assert event.outcome is AuditOutcome.ERROR and event.error == "NotFoundError"


def test_bad_inputs_raise(ctx: AppContext) -> None:
    add(ctx, rr_verified())
    uses = {"rr": {"id": "realization_rate"}}
    with pytest.raises(RenderError, match="unknown alias"):
        compile_and_run(ctx, AGENT, uses=uses, values={"other": {}})
    with pytest.raises(VariableValueError):
        compile_and_run(ctx, AGENT, uses=uses, values={"rr": {"period": "2025-Q3"}})
    with pytest.raises(RenderError):
        compile_and_run(ctx, AGENT, uses={"rr": {"identifier": "realization_rate"}})
    with pytest.raises(PolicyError):
        compile_and_run(ctx, AGENT)
    with pytest.raises(PolicyError, match="dialect"):
        compile_and_run(ctx, AGENT, uses=uses, dialect=Dialect.SNOWFLAKE)
    with pytest.raises(NotFoundError):
        compile_and_run(ctx, AGENT, uses=uses, profile="missing")


def test_definition_profile_is_the_default(ctx: AppContext) -> None:
    add(ctx, rr_verified(profile="elsewhere"))
    with pytest.raises(NotFoundError, match="elsewhere"):
        compile_and_run(ctx, AGENT, uses={"rr": {"id": "realization_rate"}})
    out = compile_and_run(ctx, AGENT, uses={"rr": {"id": "realization_rate"}}, profile="demo")
    assert out.ran


# ── recipes ──────────────────────────────────────────────────────────────────────────


def _recipe(**update) -> Recipe:
    base = Recipe(
        id="apac_q3",
        title="APAC Q3",
        uses={"rr": DefinitionRef(id="realization_rate")},
        values={"rr": Q3_APAC},
        select="SELECT region, AVG(realization_rate) AS avg_rate FROM rr GROUP BY region",
        dialect=Dialect.DUCKDB,
        state=DefinitionState.LLM_ANNOTATED,
    )
    return base.model_copy(update=update)


def test_recipe_runs_with_its_values_and_overrides(ctx: AppContext) -> None:
    add(ctx, rr_verified())
    ctx.store.add_recipe_version(_recipe(), author="analyst")
    out = compile_and_run(ctx, AGENT, recipe_id="apac_q3")
    assert out.ran and [r[0] for r in out.rows] == ["APAC"]
    assert out.columns == ["region", "avg_rate"]
    assert out.label is TrustLabel.BUILT_FROM_VERIFIED
    other = compile_and_run(ctx, AGENT, recipe_id="apac_q3", values={"rr": {"regions": ["EMEA"]}})
    assert [r[0] for r in other.rows] == ["EMEA"]
    with pytest.raises(PolicyError):
        compile_and_run(ctx, AGENT, recipe_id="apac_q3", select="SELECT 1")


def test_draft_recipe_is_refused(ctx: AppContext) -> None:
    add(ctx, rr_verified())
    ctx.store.add_recipe_version(_recipe(state=DefinitionState.DRAFT), author="analyst")
    out = compile_and_run(ctx, AGENT, recipe_id="apac_q3")
    assert not out.ran and IssueCode.DRAFT_NOT_USABLE in {i.code for i in out.issues}


# ── values used ──────────────────────────────────────────────────────────────────────


def test_values_used_echoes_given_and_default_values(ctx: AppContext) -> None:
    """An omitted changeable variable silently kept its original value; now the outcome
    says which values ran and flags each default with an info issue."""
    add(ctx, rr_verified())
    out = compile_and_run(
        ctx, AGENT, uses={"rr": {"id": "realization_rate"}}, values={"rr": {"regions": ["APAC"]}}
    )
    assert out.ran and out.usable
    used = out.values_used["rr"]
    assert used["regions"] == ["APAC"]
    assert used["period"] == {"start": "2026-01-01", "end": "2026-04-01"}
    assert "won_statuses" in used  # fixed variables are reported too
    defaults = [i for i in out.issues if i.code is IssueCode.DEFAULT_VALUE_USED]
    assert [(i.variable, i.severity) for i in defaults] == [("period", Severity.INFO)]
    assert "2026-01-01" in defaults[0].message
    assert out.label is TrustLabel.VERIFIED  # an info issue does not lower trust


def test_no_default_issue_when_every_changeable_variable_is_given(ctx: AppContext) -> None:
    add(ctx, rr_verified())
    out = compile_and_run(
        ctx, AGENT, uses={"rr": {"id": "realization_rate"}}, values={"rr": Q3_APAC}
    )
    assert not [i for i in out.issues if i.code is IssueCode.DEFAULT_VALUE_USED]
    assert out.values_used["rr"]["period"] == Q3_APAC["period"]
