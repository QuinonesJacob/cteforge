"""get_definition / definition_history, parse_query, list_audit."""

from __future__ import annotations

import datetime as dt
import hashlib
import shutil
from pathlib import Path

import pytest

from cteforge.domain.audit import AuditKind, AuditOutcome
from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import (
    NotFoundError,
    PermissionDeniedError,
    PolicyError,
    SqlParseError,
)
from cteforge.domain.issues import IssueCode, Severity
from cteforge.domain.models import Definition, DefinitionState
from cteforge.domain.users import ActorKind, User, UserRole
from cteforge.files.definitions import load_definition
from cteforge.services import _library
from cteforge.services.audit_log import list_audit
from cteforge.services.context import (
    AppConfig,
    AppContext,
    open_context,
    save_default_connection,
)
from cteforge.services.describe import definition_history, get_definition
from cteforge.services.parse_query import parse_query

ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = ROOT / "examples"
AGENT = User(name="claude-agent", role=UserRole.VIEWER, kind=ActorKind.AGENT)
APPROVER = User(name="ana", role=UserRole.APPROVER)
CONTRIBUTOR = User(name="carl", role=UserRole.CONTRIBUTOR)
SQL = (EXAMPLES / "queries" / "realization_rate.sql").read_text()


@pytest.fixture(scope="module")
def wh_file(warehouse_path: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("read_wh") / "warehouse.duckdb"
    shutil.copy(warehouse_path, path)
    return path


def _profiles(wh_file: Path) -> list[WarehouseProfile]:
    return [WarehouseProfile(name="demo", dialect=Dialect.DUCKDB, options={"path": str(wh_file)})]


@pytest.fixture
def ctx(tmp_path: Path, wh_file: Path):
    config = AppConfig(db_url=f"sqlite:///{tmp_path / 'lib.db'}")
    with open_context(config, profiles=_profiles(wh_file)) as context:
        yield context


def example(name: str, **update) -> Definition:
    return load_definition(EXAMPLES / "definitions" / f"{name}.yml").model_copy(update=update)


def last_event(ctx: AppContext):
    return ctx.audit.list_events(limit=1)[0]


# ── describe ─────────────────────────────────────────────────────────────────────────


def test_get_definition_card_with_history(ctx: AppContext) -> None:
    ctx.store.add_definition_version(example("realization_rate"), author="analyst")
    ctx.store.add_definition_version(
        example("realization_rate", version=2, keywords=["cash"]),
        author="agent",
        change_note="keywords",
        logic_changed=False,
    )
    card = get_definition(ctx, AGENT, "realization_rate")
    assert card.version == 2 and card.keywords == ["cash"]
    assert [h.version for h in card.history] == [1, 2]
    assert card.history[1].logic_changed is False and card.history[1].author == "agent"
    regions = next(v for v in card.variables if v.name == "regions")
    assert regions.allowed == ["EMEA", "NA", "APAC", "LATAM"] and regions.input_format
    assert card.template.startswith("/* Realization rate")

    v1 = get_definition(ctx, AGENT, "realization_rate", version=1)
    assert v1.version == 1 and "cash" not in v1.keywords and len(v1.history) == 2
    assert last_event(ctx).kind is AuditKind.GET_DEFINITION
    assert last_event(ctx).definitions == ["realization_rate@1"]


def test_include_source_adds_sql_and_annotations_that_parse_back(ctx: AppContext) -> None:
    ctx.store.add_definition_version(example("realization_rate"), author="analyst")
    plain = get_definition(ctx, AGENT, "realization_rate")
    assert plain.source_sql is None and plain.annotations is None
    card = get_definition(ctx, AGENT, "realization_rate", include_source=True)
    assert card.source_sql and "closed_won" in card.source_sql
    assert card.annotations is not None
    names = {a.name: a for a in card.annotations}
    assert set(names) == {v.name for v in card.variables}
    # The literal IDs address the rebuilt SQL (what the revise flow parses first).
    report = parse_query(ctx, AGENT, card.source_sql, Dialect.DUCKDB, profile="demo")
    ids = {lit["id"] for lit in report.literals} | {g["id"] for g in report.grains or []}
    assert all(set(a.literals) <= ids for a in card.annotations)


def test_drafts_can_be_described(ctx: AppContext) -> None:
    ctx.store.add_definition_version(
        example("realization_rate", state=DefinitionState.DRAFT), author="a"
    )
    assert get_definition(ctx, AGENT, "realization_rate").state is DefinitionState.DRAFT


def test_get_definition_not_found(ctx: AppContext) -> None:
    with pytest.raises(NotFoundError):
        get_definition(ctx, AGENT, "nope")
    assert last_event(ctx).error == "NotFoundError"
    ctx.store.add_definition_version(example("realization_rate"), author="a")
    with pytest.raises(NotFoundError, match="version 7"):
        get_definition(ctx, AGENT, "realization_rate", version=7)


def test_definition_history(ctx: AppContext) -> None:
    ctx.store.add_definition_version(example("booked_vs_collected"), author="a")
    [info] = definition_history(ctx, AGENT, "booked_vs_collected")
    assert info.version == 1 and info.author == "a"
    with pytest.raises(NotFoundError):
        definition_history(ctx, AGENT, "nope")


# ── parse_query ──────────────────────────────────────────────────────────────────────


def test_parse_with_warehouse_types(ctx: AppContext) -> None:
    report = parse_query(ctx, AGENT, SQL, Dialect.DUCKDB)
    assert report.schema_found and report.issues == []
    assert report.tables == ["accounts", "deals", "invoices"]
    assert report.outputs == ["month", "region", "realization_rate"]
    lits = {lit["id"]: lit for lit in report.literals}
    assert lits["closed_deals.L1"]["compared_type"] == "TIMESTAMP"
    assert lits["deals_in_region.L1"]["value"] == "EMEA"
    assert lits["deals_in_region.L1"]["column"]["type"] == "VARCHAR"
    assert report.ranges[0]["shape"] == "half_open"
    assert any(j["right_table"] == "accounts" for j in report.joins)
    assert report.grains and report.grains[0]["unit"] == "month"
    event = last_event(ctx)
    assert event.kind is AuditKind.PARSE_QUERY
    assert event.sql_sha256 == hashlib.sha256(SQL.encode()).hexdigest()
    assert "closed_won" not in event.model_dump_json()


def test_parse_dialect_mismatch_still_reads_types_with_a_warning(ctx: AppContext) -> None:
    report = parse_query(ctx, AGENT, SQL, Dialect.POSTGRES, profile="demo")
    assert report.schema_found
    assert [(i.code, i.severity) for i in report.issues] == [
        (IssueCode.DIALECT_MISMATCH, Severity.WARNING)
    ]
    lits = {lit["id"]: lit for lit in report.literals}
    assert lits["closed_deals.L1"]["compared_type"] == "TIMESTAMP"
    assert report.dialect is Dialect.POSTGRES


def test_parse_unknown_table_and_no_profile(ctx: AppContext, tmp_path: Path) -> None:
    report = parse_query(ctx, AGENT, "SELECT x FROM ghosts WHERE x = 1", Dialect.DUCKDB)
    assert not report.schema_found
    assert [i.code for i in report.issues] == [IssueCode.TYPE_UNKNOWN]
    assert "ghosts" in report.issues[0].message
    config = AppConfig(db_url=f"sqlite:///{tmp_path / 'bare.db'}")
    with open_context(config, profiles=[]) as bare:
        report = parse_query(bare, AGENT, SQL, Dialect.DUCKDB)
        assert [i.code for i in report.issues] == [IssueCode.CHECKS_INCOMPLETE]
        assert len(report.literals) == 7  # 2 dates, 2 statuses, 2 regions, paid


def test_parse_omitted_dialect_follows_the_connection(ctx: AppContext, tmp_path: Path) -> None:
    """Explicit dialect → the profile's dialect (given, else the default connection) →
    PolicyError (ADR 0021)."""
    report = parse_query(ctx, AGENT, SQL)
    assert report.dialect is Dialect.DUCKDB  # the only profile's
    assert IssueCode.DIALECT_MISMATCH not in {i.code for i in report.issues}
    explicit = parse_query(ctx, AGENT, SQL, Dialect.POSTGRES)
    assert explicit.dialect is Dialect.POSTGRES
    assert IssueCode.DIALECT_MISMATCH in {i.code for i in explicit.issues}
    assert last_event(ctx).extra["dialect"] == "postgres"
    config = AppConfig(db_url=f"sqlite:///{tmp_path / 'bare.db'}")
    with open_context(config, profiles=[]) as bare:
        with pytest.raises(PolicyError, match="ask an admin to set a default connection"):
            parse_query(bare, AGENT, SQL)
        assert parse_query(bare, AGENT, SQL, "duckdb").dialect is Dialect.DUCKDB


def test_default_connection_picks_the_profile_and_its_dialect(
    tmp_path: Path, wh_file: Path
) -> None:
    """The admin's default connection → CTEFORGE_DEFAULT_PROFILE → the only one → none;
    a stored name no longer configured falls back with a warning, never fails."""
    pg = WarehouseProfile(name="pg", dialect=Dialect.POSTGRES, options={"host": "nowhere"})
    profiles = [*_profiles(wh_file), pg]
    config = AppConfig(db_url=f"sqlite:///{tmp_path / 'two.db'}")
    with open_context(config, profiles=profiles) as two:
        with pytest.raises(PolicyError, match="No dialect given"):
            parse_query(two, AGENT, SQL)  # two profiles, no default
        save_default_connection(two, "pg")
        assert two.warehouses.default_name == "pg"
        assert _library.resolve_dialect(two, None) is Dialect.POSTGRES
        assert _library.resolve_dialect(two, None, "demo") is Dialect.DUCKDB  # profile wins
        assert parse_query(two, AGENT, SQL, profile="demo").dialect is Dialect.DUCKDB
        save_default_connection(two, "demo")
        report = parse_query(two, AGENT, SQL)
        assert (report.dialect, report.schema_found) == (Dialect.DUCKDB, True)
        two.store.set_setting("default_connection", "gone")  # removed from connections.yml
        with pytest.raises(PolicyError, match="No dialect given"):
            parse_query(two, AGENT, SQL)  # no fallback: two profiles, no env default
        assert parse_query(two, AGENT, SQL, "duckdb").issues[0].code is (
            IssueCode.DEFAULT_CONNECTION_MISSING
        )
    env_default = config.model_copy(update={"default_profile": "demo"})
    with open_context(env_default, profiles=profiles) as fallback:
        report = parse_query(fallback, AGENT, SQL)  # "gone" is still stored
        assert (report.dialect, report.schema_found) == (Dialect.DUCKDB, True)
        missing = [i for i in report.issues if i.code is IssueCode.DEFAULT_CONNECTION_MISSING]
        assert len(missing) == 1 and missing[0].severity is Severity.WARNING
        assert "'gone'" in missing[0].message and "'demo' is used instead" in missing[0].message
        save_default_connection(fallback, "pg")  # the setting beats CTEFORGE_DEFAULT_PROFILE
        assert fallback.warehouses.default_name == "pg"
        save_default_connection(fallback, None)
        assert fallback.warehouses.default_name == "demo"


def test_parse_errors(ctx: AppContext) -> None:
    with pytest.raises(SqlParseError):
        parse_query(ctx, AGENT, "SELEC FROM (", Dialect.DUCKDB)
    assert last_event(ctx).error == "SqlParseError"
    with pytest.raises(NotFoundError):
        parse_query(ctx, AGENT, SQL, Dialect.DUCKDB, profile="nope")


# ── audit log ────────────────────────────────────────────────────────────────────────


def test_list_audit_is_for_approvers_and_admins(ctx: AppContext) -> None:
    for user in (AGENT, CONTRIBUTOR):
        with pytest.raises(PermissionDeniedError):
            list_audit(ctx, user)
    event = last_event(ctx)
    assert event.kind is AuditKind.ADMIN and event.outcome is AuditOutcome.REJECTED
    assert event.actor == "carl"


def test_list_audit_filters_and_pages(ctx: AppContext) -> None:
    ctx.store.add_definition_version(example("realization_rate"), author="a")
    start = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
    for _ in range(3):
        get_definition(ctx, AGENT, "realization_rate")
    parse_query(ctx, CONTRIBUTOR, SQL, Dialect.DUCKDB)

    page = list_audit(ctx, APPROVER, limit=2)
    assert len(page.events) == 2 and page.has_more and page.limit == 2
    assert page.events[0].kind is AuditKind.PARSE_QUERY  # newest first

    gets = list_audit(ctx, APPROVER, kind=AuditKind.GET_DEFINITION)
    assert len(gets.events) == 3 and not gets.has_more
    assert list_audit(ctx, APPROVER, actor="carl").events[0].kind is AuditKind.PARSE_QUERY
    assert len(list_audit(ctx, APPROVER, definition_id="realization_rate").events) == 3
    assert len(list_audit(ctx, APPROVER, since=start, kind=AuditKind.PARSE_QUERY).events) == 1
    # Listing is itself recorded, after the read.
    assert last_event(ctx).extra["op"] == "list_audit"


def test_list_audit_bad_filters_are_checked_after_permission_and_audited(
    ctx: AppContext,
) -> None:
    """Regression: an invalid kind raised a bare ValueError before the permission check
    (unaudited); a naive `since` surfaced as AuditError."""
    from cteforge.domain.errors import PolicyError

    with pytest.raises(PermissionDeniedError):
        list_audit(ctx, AGENT, kind="bogus")  # type: ignore[arg-type]
    assert last_event(ctx).outcome is AuditOutcome.REJECTED and last_event(ctx).actor == (
        "claude-agent"
    )
    with pytest.raises(PolicyError, match="kind"):
        list_audit(ctx, APPROVER, kind="bogus")  # type: ignore[arg-type]
    event = last_event(ctx)
    assert event.outcome is AuditOutcome.REJECTED and event.extra["op"] == "list_audit"
    with pytest.raises(PolicyError, match="timezone"):
        list_audit(ctx, APPROVER, since=dt.datetime(2020, 1, 1))
    assert last_event(ctx).outcome is AuditOutcome.REJECTED
    assert list_audit(ctx, APPROVER, kind="get_definition").events == []  # type: ignore[arg-type]
