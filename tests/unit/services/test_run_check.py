"""check_and_run: exploratory raw SQL on a DuckDB profile (copy of the demo warehouse)."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from cteforge.domain.audit import AuditKind, AuditOutcome
from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import NotFoundError, SqlParseError
from cteforge.domain.issues import IssueCode
from cteforge.domain.models import JoinEdge, JoinKey, TeamMode, TrustLabel
from cteforge.domain.users import ActorKind, User, UserRole
from cteforge.services.context import AppConfig, AppContext, open_context, set_team_mode
from cteforge.services.run import check_and_run

AGENT = User(name="claude-agent", role=UserRole.VIEWER, kind=ActorKind.AGENT)
JOIN_SQL = (
    "SELECT a.region, COUNT(*) AS deals FROM deals AS d "
    "JOIN accounts AS a ON a.account_id = d.account_id GROUP BY a.region ORDER BY a.region"
)


@pytest.fixture(scope="module")
def wh_file(warehouse_path: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("check_wh") / "warehouse.duckdb"
    shutil.copy(warehouse_path, path)
    return path


@pytest.fixture
def ctx(tmp_path: Path, wh_file: Path):
    profile = WarehouseProfile(name="demo", dialect=Dialect.DUCKDB, options={"path": str(wh_file)})
    config = AppConfig(db_url=f"sqlite:///{tmp_path / 'lib.db'}")
    with open_context(config, profiles=[profile]) as context:
        yield context


def last_event(ctx: AppContext):
    return ctx.audit.list_events(limit=1)[0]


def _edge() -> JoinEdge:
    return JoinEdge(
        id="deals__accounts__account_id",
        left_table="deals",
        right_table="accounts",
        keys=[JoinKey(left="account_id", right="account_id")],
    )


def test_simple_select_runs_tagged_exploratory(ctx: AppContext) -> None:
    out = check_and_run(
        ctx, AGENT, "SELECT region, COUNT(*) AS n FROM accounts GROUP BY 1 ORDER BY 1"
    )
    assert out.ran and out.usable
    assert out.label is TrustLabel.NEW_LOGIC_KNOWN_JOINS  # no joins at all
    assert out.columns == ["region", "n"]
    assert [r[0] for r in out.rows] == ["APAC", "EMEA", "LATAM", "NA"]
    assert sum(r[1] for r in out.rows) == 200
    assert out.sql.startswith(f"/* cteforge: exploratory compile_id={out.compile_id} */\n")
    assert out.definitions == []


def test_join_not_in_join_map_is_exploratory(ctx: AppContext) -> None:
    out = check_and_run(ctx, AGENT, JOIN_SQL)
    assert out.ran and out.label is TrustLabel.EXPLORATORY
    unknown = [i for i in out.issues if i.code is IssueCode.UNKNOWN_JOIN]
    assert unknown and "deals" in unknown[0].message and "accounts" in unknown[0].message


def test_join_in_join_map_is_new_logic_on_known_joins(ctx: AppContext) -> None:
    ctx.store.upsert_join(_edge())
    out = check_and_run(ctx, AGENT, JOIN_SQL)
    assert out.ran and out.label is TrustLabel.NEW_LOGIC_KNOWN_JOINS
    assert IssueCode.UNKNOWN_JOIN not in {i.code for i in out.issues}


def test_join_on_other_keys_than_the_edge_is_unknown(ctx: AppContext) -> None:
    ctx.store.upsert_join(_edge())
    sql = "SELECT COUNT(*) FROM deals AS d JOIN accounts AS a ON a.account_id = d.deal_id"
    out = check_and_run(ctx, AGENT, sql)
    assert out.label is TrustLabel.EXPLORATORY


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM deals",
        "WITH x AS (DELETE FROM deals RETURNING *) SELECT * FROM x",
        "CREATE TABLE t AS SELECT 1",
        "SELECT * INTO t2 FROM deals",
    ],
)
def test_not_read_only_is_refused(ctx: AppContext, sql: str) -> None:
    out = check_and_run(ctx, AGENT, sql)
    assert not out.ran and not out.usable and out.sql == "" and out.rows == []
    assert IssueCode.NOT_READ_ONLY in {i.code for i in out.issues}
    event = last_event(ctx)
    assert event.kind is AuditKind.CHECK_AND_RUN and event.outcome is AuditOutcome.REJECTED
    assert event.compile_id is None


def test_multi_statement_is_refused(ctx: AppContext) -> None:
    with pytest.raises(SqlParseError, match="exactly one"):
        check_and_run(ctx, AGENT, "SELECT 1; DELETE FROM deals")
    event = last_event(ctx)
    assert event.outcome is AuditOutcome.REJECTED and event.error == "SqlParseError"
    # The DELETE never ran.
    assert check_and_run(ctx, AGENT, "SELECT COUNT(*) FROM deals").rows == [[2000]]


def test_strict_mode_blocks_raw_sql(ctx: AppContext) -> None:
    set_team_mode(ctx, TeamMode.STRICT)
    out = check_and_run(ctx, AGENT, "SELECT 1 AS x")
    assert not out.ran and IssueCode.STRICT_MODE_BLOCKED in {i.code for i in out.issues}
    assert last_event(ctx).outcome is AuditOutcome.REJECTED


def test_row_limit(ctx: AppContext) -> None:
    out = check_and_run(ctx, AGENT, "SELECT deal_id FROM deals ORDER BY deal_id", limit=5)
    assert out.rows == [[1], [2], [3], [4], [5]] and out.truncated
    assert IssueCode.ROW_LIMIT_REACHED in {i.code for i in out.issues}


def test_bad_sql_returns_run_failed_without_leaking_into_audit(ctx: AppContext) -> None:
    out = check_and_run(ctx, AGENT, "SELECT secret_col FROM deals WHERE status = 'vip'")
    assert not out.ran
    assert IssueCode.RUN_FAILED in {i.code for i in out.issues}
    event = last_event(ctx)
    assert event.outcome is AuditOutcome.ERROR and event.error == "WarehouseError"
    assert event.sql_sha256 == hashlib.sha256(out.sql.encode()).hexdigest()
    dumped = event.model_dump_json()
    assert "secret_col" not in dumped and "vip" not in dumped


def test_run_false_explains_only(ctx: AppContext) -> None:
    out = check_and_run(ctx, AGENT, "SELECT * FROM deals", run=False)
    assert out.usable and not out.ran and out.rows == [] and out.compile_id
    assert last_event(ctx).row_count is None


def test_unknown_profile(ctx: AppContext) -> None:
    with pytest.raises(NotFoundError):
        check_and_run(ctx, AGENT, "SELECT 1", profile="nope")
    assert last_event(ctx).outcome is AuditOutcome.ERROR


def test_lateral_cross_product_is_exploratory_even_with_known_edge(ctx: AppContext) -> None:
    """Regression: tables read with no known join between them (LATERAL) were labeled
    new_logic_on_known_joins because only detected joins were checked."""
    ctx.store.upsert_join(_edge())
    sql = (
        "SELECT COUNT(*) AS n FROM deals d, LATERAL (SELECT * FROM accounts a "
        "WHERE a.account_id <> d.account_id) x"
    )
    out = check_and_run(ctx, AGENT, sql)
    assert out.label is TrustLabel.EXPLORATORY
    assert IssueCode.UNKNOWN_JOIN in {i.code for i in out.issues}


def test_comma_join_of_two_tables_is_exploratory(ctx: AppContext) -> None:
    ctx.store.upsert_join(_edge())
    out = check_and_run(ctx, AGENT, "SELECT COUNT(*) FROM deals, accounts")
    assert out.label is TrustLabel.EXPLORATORY


def test_known_join_still_connects_its_tables(ctx: AppContext) -> None:
    ctx.store.upsert_join(_edge())
    out = check_and_run(ctx, AGENT, JOIN_SQL)
    assert out.label is TrustLabel.NEW_LOGIC_KNOWN_JOINS
    assert out.issues == []


def test_draft_join_edge_is_not_known(ctx: AppContext) -> None:
    from cteforge.domain.models import DefinitionState

    ctx.store.upsert_join(_edge().model_copy(update={"state": DefinitionState.DRAFT}))
    out = check_and_run(ctx, AGENT, JOIN_SQL)
    assert out.label is TrustLabel.EXPLORATORY


def test_concurrent_runs_never_mix_result_sets(ctx: AppContext) -> None:
    """Regression: the one DuckDB connection was shared by concurrent callers, so a thread
    could read another thread's rows or an EXPLAIN plan."""
    import threading

    bad: list[object] = []

    def worker(i: int) -> None:
        for _ in range(15):
            out = check_and_run(ctx, AGENT, f"SELECT {i} AS who, COUNT(*) AS n FROM deals")
            if not out.ran or out.columns != ["who", "n"] or out.rows != [[i, 2000]]:
                bad.append((i, out.columns, out.rows))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert bad == []


# ── Joins through CTEs, failed runs, result size ─────────────────────────────────────

CTE_SQL = Path(__file__).resolve().parents[3] / "examples" / "queries" / "realization_rate.sql"


def _invoice_edge() -> JoinEdge:
    return JoinEdge(
        id="deals__invoices__deal_id",
        left_table="deals",
        right_table="invoices",
        keys=[JoinKey(left="deal_id", right="deal_id")],
    )


def test_joins_through_ctes_are_matched_on_their_base_tables(ctx: AppContext) -> None:
    """Exploratory SQL written with CTEs is judged like join mining judges it: on the base
    tables and columns behind each side, not on the CTE names."""
    ctx.store.upsert_join(_edge())
    ctx.store.upsert_join(_invoice_edge())
    out = check_and_run(ctx, AGENT, CTE_SQL.read_text(), run=False)
    assert [i for i in out.issues if i.code is IssueCode.UNKNOWN_JOIN] == []
    assert out.label is TrustLabel.NEW_LOGIC_KNOWN_JOINS


def test_cte_join_without_its_edge_names_the_base_tables(ctx: AppContext) -> None:
    ctx.store.upsert_join(_edge())  # deals↔accounts known, deals↔invoices not
    out = check_and_run(ctx, AGENT, CTE_SQL.read_text(), run=False)
    unknown = [i.message for i in out.issues if i.code is IssueCode.UNKNOWN_JOIN]
    assert out.label is TrustLabel.EXPLORATORY
    assert any("'deals' and 'invoices'" in m for m in unknown), unknown
    assert not any("'closed_deals'" in m for m in unknown), unknown


def test_warehouse_failure_is_not_usable(ctx: AppContext) -> None:
    """A query the warehouse rejects has no answer: never `usable` with empty rows."""
    out = check_and_run(ctx, AGENT, "SELECT close_date, value FROM deals")
    assert not out.ran and out.rows == []
    assert out.usable is False
    assert [i.code for i in out.issues] == [IssueCode.RUN_FAILED]
    assert any("could not compile or run" in r for r in out.reasons)


def test_wide_results_are_capped_by_size(ctx: AppContext, monkeypatch) -> None:
    from cteforge.services import run as run_module

    monkeypatch.setattr(run_module, "MAX_RESULT_BYTES", 5_000)
    out = check_and_run(
        ctx, AGENT, "SELECT repeat('x', 1000) AS wide FROM range(50) t(x)", limit=50
    )
    assert out.ran and out.usable and out.truncated
    assert 0 < out.row_count < 50 and out.row_count == len(out.rows)
    assert IssueCode.ROW_LIMIT_REACHED in {i.code for i in out.issues}


# ── a definition's original SQL → hint to compile_and_run ─────────────────────────────


def _propose_realization_rate(ctx: AppContext) -> str:
    import yaml

    from cteforge.services.propose import propose_definition

    examples = Path(__file__).resolve().parents[3] / "examples"
    sql = (examples / "queries" / "realization_rate.sql").read_text()
    ann = yaml.safe_load(
        (examples / "definitions" / "realization_rate.annotations.yml").read_text()
    )
    admin = User(name="root", role=UserRole.ADMIN)
    out = propose_definition(
        ctx,
        admin,
        sql=sql,
        dialect="duckdb",
        metadata={k: v for k, v in ann.items() if k != "variables"},
        annotations=ann["variables"],
    )
    assert out.auto_applied
    return sql


def test_definition_sql_gets_a_compile_and_run_hint(ctx: AppContext) -> None:
    sql = _propose_realization_rate(ctx)
    reformatted = "\n".join(line.strip() for line in sql.splitlines())  # same AST
    out = check_and_run(ctx, AGENT, reformatted, run=False)
    hints = [i for i in out.issues if i.code is IssueCode.MATCHES_DEFINITION]
    assert len(hints) == 1 and hints[0].severity == "info"
    assert "'realization_rate'" in hints[0].message and "compile_and_run" in hints[0].message
    # The hint never raises the label: raw SQL stays raw SQL.
    assert out.label in (TrustLabel.NEW_LOGIC_KNOWN_JOINS, TrustLabel.EXPLORATORY)


def test_changed_sql_gets_no_hint(ctx: AppContext) -> None:
    sql = _propose_realization_rate(ctx)
    changed = sql.rstrip().rstrip(";") + "\nLIMIT 5"
    out = check_and_run(ctx, AGENT, changed, run=False)
    assert IssueCode.MATCHES_DEFINITION not in {i.code for i in out.issues}
    other = check_and_run(ctx, AGENT, JOIN_SQL, run=False)
    assert IssueCode.MATCHES_DEFINITION not in {i.code for i in other.issues}
