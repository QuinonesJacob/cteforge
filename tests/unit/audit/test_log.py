from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path
from typing import Any

import pytest

from cteforge.audit import AuditLog
from cteforge.domain.audit import AuditEvent, AuditKind, AuditOutcome
from cteforge.domain.errors import AuditError
from cteforge.domain.models import TrustLabel
from cteforge.domain.users import ActorKind

T0 = dt.datetime(2026, 11, 16, 12, 0, tzinfo=dt.UTC)


def make_event(minutes: int = 0, **overrides: Any) -> AuditEvent:
    data: dict[str, Any] = {
        "id": uuid.uuid4().hex,
        "at": T0 + dt.timedelta(minutes=minutes),
        "actor": "jq",
        "actor_kind": ActorKind.HUMAN,
        "kind": AuditKind.SEARCH,
    }
    data.update(overrides)
    return AuditEvent(**data)


@pytest.fixture
def log() -> AuditLog:
    audit = AuditLog("sqlite://")
    audit.init_schema()
    return audit


def test_roundtrip_preserves_all_fields(log: AuditLog) -> None:
    event = make_event(
        kind=AuditKind.COMPILE_AND_RUN,
        actor_kind=ActorKind.AGENT,
        question="What is realization rate?",
        definitions=["realization_rate@3", "hours@1"],
        values={"start": "2026-01-01", "regions": ["EU", "US"], "n": 5},
        trust_label=TrustLabel.VERIFIED,
        compile_id="8f2a00ab12cd",
        sql_sha256="a" * 64,
        row_count=42,
        duration_ms=120,
        outcome=AuditOutcome.ERROR,
        error="timeout",
        extra={"client": "mcp"},
    )
    log.record(event)
    assert log.list_events() == [event]


def test_in_memory_url_persists_across_calls() -> None:
    audit = AuditLog("sqlite://")
    audit.init_schema()
    audit.record(make_event(0))
    audit.record(make_event(1))
    assert len(audit.list_events()) == 2


def test_file_database_is_shared_between_instances(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'cteforge.db'}"
    first = AuditLog(url)
    first.init_schema()
    event = make_event()
    first.record(event)
    second = AuditLog(url)
    second.init_schema()
    assert second.list_events() == [event]


def test_init_schema_is_idempotent_and_keeps_data(log: AuditLog) -> None:
    event = make_event()
    log.record(event)
    log.init_schema()
    log.init_schema()
    assert log.list_events() == [event]


def test_record_without_schema_raises_audit_error() -> None:
    with pytest.raises(AuditError):
        AuditLog("sqlite://").record(make_event())


def test_duplicate_id_raises_audit_error(log: AuditLog) -> None:
    event = make_event()
    log.record(event)
    with pytest.raises(AuditError):
        log.record(event)


SECRET_QUESTION = "what is revenue for ACME secret"
SECRET_VALUE = "ACME secret value"


def _assert_no_leak(message: str) -> None:
    assert "ACME" not in message
    assert "parameters" not in message
    assert "INSERT" not in message.upper() and "SELECT" not in message.upper()


def test_record_error_does_not_leak_question_or_values(log: AuditLog) -> None:
    event = make_event(question=SECRET_QUESTION, values={"customer": SECRET_VALUE})
    log.record(event)
    with pytest.raises(AuditError) as info:
        log.record(event)
    _assert_no_leak(str(info.value))
    assert "IntegrityError" in str(info.value)


def test_list_events_error_does_not_leak_filter_values() -> None:
    with pytest.raises(AuditError) as info:
        AuditLog("sqlite://").list_events(actor=SECRET_VALUE)
    _assert_no_leak(str(info.value))
    assert "OperationalError" in str(info.value)


def test_init_schema_error_names_only_exception_class(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'missing-dir' / 'cteforge.db'}"
    with pytest.raises(AuditError) as info:
        AuditLog(url).init_schema()
    assert str(info.value) == "could not create audit schema (OperationalError)"


def test_invalid_url_raises_audit_error() -> None:
    with pytest.raises(AuditError):
        AuditLog("not a url")


def test_rejects_naive_event_time(log: AuditLog) -> None:
    with pytest.raises(AuditError):
        log.record(make_event(at=dt.datetime(2026, 11, 16, 12, 0)))
    assert log.list_events() == []


def test_rejects_sql_text_in_sql_sha256(log: AuditLog) -> None:
    with pytest.raises(AuditError):
        log.record(make_event(sql_sha256="SELECT * FROM secrets"))
    assert log.list_events() == []


def test_rejects_non_hex_compile_id(log: AuditLog) -> None:
    with pytest.raises(AuditError):
        log.record(make_event(compile_id="x*/ DROP"))


def test_newest_first_and_limit(log: AuditLog) -> None:
    events = [make_event(m) for m in (5, 1, 9, 3)]
    for event in events:
        log.record(event)
    listed = log.list_events()
    assert [e.at for e in listed] == sorted((e.at for e in events), reverse=True)
    assert [e.id for e in log.list_events(limit=2)] == [e.id for e in listed[:2]]
    assert log.list_events(limit=0) == []


def test_same_timestamp_orders_by_insertion_newest_first(log: AuditLog) -> None:
    first, second = make_event(0), make_event(0)
    log.record(first)
    log.record(second)
    assert [e.id for e in log.list_events()] == [second.id, first.id]


def test_non_utc_times_are_normalized_and_ordered(log: AuditLog) -> None:
    plus5 = dt.timezone(dt.timedelta(hours=5))
    minus5 = dt.timezone(dt.timedelta(hours=-5))
    early = make_event(at=dt.datetime(2026, 11, 16, 14, 0, tzinfo=plus5))  # 09:00Z
    late = make_event(at=dt.datetime(2026, 11, 16, 8, 0, tzinfo=minus5))  # 13:00Z
    log.record(late)
    log.record(early)
    listed = log.list_events()
    assert [e.id for e in listed] == [late.id, early.id]
    assert listed[0].at == late.at
    assert listed[0].at.utcoffset() == dt.timedelta(0)


def test_filter_actor(log: AuditLog) -> None:
    mine = make_event(0, actor="jq")
    log.record(mine)
    log.record(make_event(1, actor="claude-agent"))
    assert log.list_events(actor="jq") == [mine]
    assert log.list_events(actor="nobody") == []


def test_filter_kind(log: AuditLog) -> None:
    run = make_event(0, kind=AuditKind.COMPILE_AND_RUN)
    log.record(run)
    log.record(make_event(1, kind=AuditKind.SEARCH))
    assert log.list_events(kind=AuditKind.COMPILE_AND_RUN) == [run]


def test_filter_since_is_inclusive_and_timezone_aware(log: AuditLog) -> None:
    old, edge, new = make_event(0), make_event(10), make_event(20)
    for event in (old, edge, new):
        log.record(event)
    since = (T0 + dt.timedelta(minutes=10)).astimezone(dt.timezone(dt.timedelta(hours=-8)))
    assert [e.id for e in log.list_events(since=since)] == [new.id, edge.id]


def test_filter_since_rejects_naive_datetime(log: AuditLog) -> None:
    with pytest.raises(AuditError):
        log.list_events(since=dt.datetime(2026, 1, 1))


def test_filter_definition_id_matches_any_entry_by_exact_id(log: AuditLog) -> None:
    hit_first = make_event(0, definitions=["rate@1", "hours@2"])
    hit_second = make_event(1, definitions=["hours@2", "rate@3"])
    log.record(hit_first)
    log.record(hit_second)
    log.record(make_event(2, definitions=["rate_v2@1"]))  # different id, shared prefix
    log.record(make_event(3, definitions=["my_rate@1"]))  # id contains "rate@" after a char
    log.record(make_event(4, definitions=[]))
    assert [e.id for e in log.list_events(definition_id="rate")] == [
        hit_second.id,
        hit_first.id,
    ]


def test_filter_definition_id_treats_like_wildcards_literally(log: AuditLog) -> None:
    log.record(make_event(0, definitions=["ab@1"]))
    log.record(make_event(1, definitions=["axb@1"]))
    assert log.list_events(definition_id="a_b") == []
    assert log.list_events(definition_id="a%") == []
    assert log.list_events(definition_id="%") == []


def test_filter_definition_id_ignores_quote_injection_in_stored_entries(log: AuditLog) -> None:
    # A stored entry containing a quote must not fake a match for another id.
    hostile = make_event(0, definitions=['x", "rate@1'])
    log.record(hostile)
    real = make_event(1, definitions=["rate@1"])
    log.record(real)
    assert log.list_events(definition_id="rate") == [real]
    # Matched literally against the whole entry prefix, quotes included.
    assert log.list_events(definition_id='x", "rate') == [hostile]


def test_filter_definition_id_respects_limit_after_python_check(log: AuditLog) -> None:
    for minute in range(5):
        log.record(make_event(minute, definitions=['q", "rate@1']))  # prefilter-only matches
    hits = [make_event(10 + m, definitions=["rate@1"]) for m in range(3)]
    for event in hits:
        log.record(event)
    assert [e.id for e in log.list_events(definition_id="rate", limit=2)] == [
        hits[2].id,
        hits[1].id,
    ]


def test_filters_combine(log: AuditLog) -> None:
    target = make_event(
        30, actor="claude-agent", kind=AuditKind.COMPILE_AND_RUN, definitions=["rate@3"]
    )
    log.record(target)
    log.record(make_event(31, actor="jq", kind=AuditKind.COMPILE_AND_RUN, definitions=["rate@3"]))
    log.record(make_event(32, actor="claude-agent", kind=AuditKind.SEARCH, definitions=["rate@3"]))
    log.record(
        make_event(33, actor="claude-agent", kind=AuditKind.COMPILE_AND_RUN, definitions=["x@1"])
    )
    log.record(
        make_event(1, actor="claude-agent", kind=AuditKind.COMPILE_AND_RUN, definitions=["rate@2"])
    )
    result = log.list_events(
        actor="claude-agent",
        kind=AuditKind.COMPILE_AND_RUN,
        definition_id="rate",
        since=T0 + dt.timedelta(minutes=10),
    )
    assert result == [target]
