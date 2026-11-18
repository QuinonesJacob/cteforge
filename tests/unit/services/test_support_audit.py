from __future__ import annotations

import datetime as dt
import hashlib
import logging
from collections.abc import Iterator
from decimal import Decimal

import pytest

from cteforge.domain.audit import AuditEvent, AuditKind, AuditOutcome
from cteforge.domain.errors import AuditError, WarehouseError
from cteforge.domain.models import TrustLabel
from cteforge.domain.users import ActorKind, User, UserRole
from cteforge.services._audit import build_event, error_summary, record, sql_sha256
from cteforge.services.context import AppConfig, AppContext, open_context

FIXED = dt.datetime(2026, 11, 16, 10, 0, tzinfo=dt.UTC)
SQL = "SELECT * FROM deals WHERE region = 'EMEA-secret-value'"
AGENT = User(name="bot", role=UserRole.VIEWER, kind=ActorKind.AGENT)


@pytest.fixture
def ctx() -> Iterator[AppContext]:
    with open_context(AppConfig(db_url="sqlite://"), clock=lambda: FIXED) as c:
        yield c


class BrokenAudit:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def record(self, event: AuditEvent) -> None:
        raise self.exc

    def close(self) -> None:
        pass


def test_sql_sha256() -> None:
    assert sql_sha256(SQL) == hashlib.sha256(SQL.encode()).hexdigest()


def test_record_writes_event_with_actor_clock_and_hash(ctx: AppContext) -> None:
    event = record(
        ctx,
        AGENT,
        AuditKind.COMPILE_AND_RUN,
        sql=SQL,
        question="how much?",
        definitions=["realization_rate@1"],
        values={"amount": Decimal("1000.5"), "on": dt.date(2026, 1, 1)},
        trust_label=TrustLabel.VERIFIED,
        compile_id="abc123",
        row_count=4,
        duration_ms=12,
    )
    assert event is not None
    (stored,) = ctx.audit.list_events()
    assert stored == event
    assert len(stored.id) == 32 and stored.at == FIXED
    assert (stored.actor, stored.actor_kind) == ("bot", ActorKind.AGENT)
    assert stored.sql_sha256 == sql_sha256(SQL)
    assert stored.values == {"amount": 1000.5, "on": "2026-01-01"}
    assert stored.outcome is AuditOutcome.OK and stored.error is None
    assert "EMEA-secret-value" not in stored.model_dump_json()


def test_error_is_stored_as_class_name_only(ctx: AppContext) -> None:
    exc = WarehouseError(f"Parser Error near {SQL}")
    event = record(ctx, AGENT, AuditKind.CHECK_AND_RUN, sql=SQL, error=exc)
    assert event is not None
    assert event.error == "WarehouseError" and event.outcome is AuditOutcome.ERROR
    assert "EMEA-secret-value" not in ctx.audit.list_events()[0].model_dump_json()
    assert error_summary("fixed text") == "fixed text" and error_summary(None) is None


def test_explicit_outcome_wins(ctx: AppContext) -> None:
    event = record(
        ctx, AGENT, AuditKind.PROPOSE, error="permission denied", outcome=AuditOutcome.REJECTED
    )
    assert event is not None and event.outcome is AuditOutcome.REJECTED


def test_reserved_fields_are_refused(ctx: AppContext) -> None:
    with pytest.raises(AuditError, match="actor"):
        build_event(ctx, AGENT, AuditKind.SEARCH, actor="someone-else")
    with pytest.raises(AuditError, match="sql_sha256"):
        build_event(ctx, AGENT, AuditKind.SEARCH, sql_sha256="0" * 64)


def test_invalid_fields_do_not_leak_values(ctx: AppContext) -> None:
    with pytest.raises(AuditError) as info:
        build_event(ctx, AGENT, AuditKind.SEARCH, sql_text=SQL)
    assert "EMEA-secret-value" not in str(info.value)
    assert "sql_text" in str(info.value)
    with pytest.raises(AuditError) as info2:
        build_event(ctx, AGENT, AuditKind.SEARCH, row_count=SQL)
    assert "EMEA-secret-value" not in str(info2.value)


def test_fail_closed_raises_audit_error(ctx: AppContext) -> None:
    ctx.audit = BrokenAudit(RuntimeError(f"db down while inserting {SQL}"))  # type: ignore[assignment]
    with pytest.raises(AuditError) as info:
        record(ctx, AGENT, AuditKind.COMPILE_AND_RUN, sql=SQL)
    assert "EMEA-secret-value" not in str(info.value)
    assert "RuntimeError" in str(info.value)


def test_fail_closed_passes_audit_error_through(ctx: AppContext) -> None:
    ctx.audit = BrokenAudit(AuditError("could not record audit event x (OperationalError)"))  # type: ignore[assignment]
    with pytest.raises(AuditError, match="OperationalError"):
        record(ctx, AGENT, AuditKind.COMPILE_AND_RUN)


@pytest.mark.usefixtures("mcp_surface")  # reads are logged through MCP (ADR 0029)
def test_fail_open_logs_class_only_and_returns_none(
    ctx: AppContext, caplog: pytest.LogCaptureFixture
) -> None:
    ctx.audit = BrokenAudit(RuntimeError(f"boom {SQL}"))  # type: ignore[assignment]
    with caplog.at_level(logging.WARNING, logger="cteforge.services._audit"):
        assert record(ctx, AGENT, AuditKind.SEARCH, fail_closed=False, sql=SQL) is None
    assert "RuntimeError" in caplog.text
    assert "EMEA-secret-value" not in caplog.text


def test_human_actor_kind(ctx: AppContext) -> None:
    human = User(name="jq", role=UserRole.ADMIN)
    event = record(ctx, human, AuditKind.ADMIN, extra={"changed": "mode"})
    assert event is not None and event.actor_kind is ActorKind.HUMAN
    assert ctx.audit.list_events(actor="jq")[0].extra == {"changed": "mode"}


def test_events_name_the_calling_token_by_id_and_prefix_only(ctx: AppContext) -> None:
    """ADR 0029: the Log shows which token made a call (id + 12-char prefix)."""
    caller = User(
        name="bot",
        role=UserRole.VIEWER,
        kind=ActorKind.AGENT,
        token_id="tok-1",
        token_prefix="cfg_pat_abcd",
    )
    event = build_event(ctx, caller, AuditKind.SEARCH, extra={"op": "list_library"})
    assert event.extra == {
        "op": "list_library",
        "token_id": "tok-1",
        "token_prefix": "cfg_pat_abcd",
    }
    no_token = build_event(ctx, caller.model_copy(update={"token_id": None}), AuditKind.SEARCH)
    assert no_token.extra == {}


def test_every_use_case_is_classified_read_run_write_or_unaudited() -> None:
    """ADR 0029: what is logged depends on one explicit classification (_audit); every
    public use-case (services module function taking ctx and user) must be in it."""
    import importlib
    import inspect
    import pkgutil

    import cteforge.services as services
    from cteforge.services import _audit

    support = {"context", "connections", "auth", "cards", "import_query", "render_definition"}
    classes = [
        _audit.READ_USE_CASES,
        _audit.RUN_USE_CASES,
        _audit.WRITE_USE_CASES,
        _audit.UNAUDITED_USE_CASES,
    ]
    found: set[str] = set()
    for info in pkgutil.iter_modules(services.__path__):
        if info.name.startswith("_") or info.name in support:
            continue
        module = importlib.import_module(f"cteforge.services.{info.name}")
        for name, fn in inspect.getmembers(module, inspect.isfunction):
            if fn.__module__ != module.__name__ or name.startswith("_"):
                continue
            params = list(inspect.signature(fn).parameters)
            if params[:1] == ["ctx"] and (params[1:2] == ["user"] or name == "bootstrap_admin"):
                found.add(name)
    assert found, "no use-cases found"
    unclassified = sorted(n for n in found if not any(n in c for c in classes))
    assert unclassified == [], f"classify these use-cases in services/_audit.py: {unclassified}"
    twice = [n for n in found if sum(n in c for c in classes) > 1]
    assert twice == []
    assert not (set().union(*classes) - found - {"search"}), "stale names in the classification"


def test_successful_reads_are_logged_only_through_mcp(ctx: AppContext) -> None:
    from cteforge.services._audit import is_read, surface

    assert is_read(AuditKind.SEARCH, {"op": "list_library"}) and is_read(AuditKind.PARSE_QUERY, {})
    assert not is_read(AuditKind.COMPILE_AND_RUN, {"op": "compile_and_run"})
    assert not is_read(AuditKind.ADMIN, {"op": "create_token"})
    assert record(ctx, AGENT, AuditKind.SEARCH, fail_closed=False) is None  # API read
    with surface("cli"):
        assert record(ctx, AGENT, AuditKind.GET_DEFINITION, extra={"op": "list_joins"}) is None
    assert ctx.audit.list_events() == []
    with surface("mcp"):
        assert record(ctx, AGENT, AuditKind.SEARCH, fail_closed=False) is not None
    refused = record(ctx, AGENT, AuditKind.SEARCH, outcome=AuditOutcome.REJECTED)
    run = record(ctx, AGENT, AuditKind.COMPILE_AND_RUN)
    change = record(ctx, AGENT, AuditKind.ADMIN, extra={"op": "update_profile"})
    assert None not in (refused, run, change)
    assert len(ctx.audit.list_events()) == 4
