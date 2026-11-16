from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from cteforge.domain.audit import AuditEvent, AuditKind
from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import CodecError, NotFoundError, PolicyError
from cteforge.domain.models import TeamMode
from cteforge.domain.users import ActorKind
from cteforge.services.context import (
    MODE_SETTING,
    AppConfig,
    AuthMode,
    open_context,
    redact_db_url,
    set_team_mode,
    team_mode,
)

FIXED = dt.datetime(2026, 11, 16, 12, 0, tzinfo=dt.UTC)


def test_from_env_defaults() -> None:
    config = AppConfig.from_env({})
    assert config.db_url == "sqlite:///cteforge.db"
    assert config.connections_path is None
    assert config.default_profile is None
    assert config.auth is AuthMode.ON and config.auth_enabled
    assert config.user == "local"


def test_from_env_reads_every_variable(tmp_path: Path) -> None:
    config = AppConfig.from_env(
        {
            "CTEFORGE_DB_URL": "sqlite://",
            "CTEFORGE_CONNECTIONS": str(tmp_path / "c.yml"),
            "CTEFORGE_DEFAULT_PROFILE": "demo",
            "CTEFORGE_AUTH": "OFF",
            "CTEFORGE_USER": " jq ",
        }
    )
    assert config.db_url == "sqlite://"
    assert config.connections_path == tmp_path / "c.yml"
    assert config.default_profile == "demo"
    assert config.auth is AuthMode.OFF and not config.auth_enabled
    assert config.user == "jq"


def test_from_env_blank_values_count_as_unset() -> None:
    config = AppConfig.from_env({"CTEFORGE_USER": "  ", "CTEFORGE_DB_URL": ""})
    assert config.user == "local" and config.db_url == "sqlite:///cteforge.db"


def test_from_env_reads_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CTEFORGE_USER", "from-env")
    monkeypatch.setenv("CTEFORGE_AUTH", "off")
    config = AppConfig.from_env()
    assert config.user == "from-env" and config.auth is AuthMode.OFF


def test_invalid_auth_value_is_refused() -> None:
    with pytest.raises(PolicyError, match="CTEFORGE_AUTH"):
        AppConfig.from_env({"CTEFORGE_AUTH": "maybe"})


def test_config_rejects_blank_user() -> None:
    with pytest.raises(ValidationError):
        AppConfig(user=" ")


def test_open_context_in_memory_store_and_audit() -> None:
    ctx = open_context(AppConfig(db_url="sqlite://"), clock=lambda: FIXED)
    try:
        assert ctx.now() == FIXED
        assert ctx.store.list_definitions() == []
        assert ctx.warehouses.names() == []
        event = AuditEvent(
            id="e1", at=ctx.now(), actor="a", actor_kind=ActorKind.HUMAN, kind=AuditKind.SEARCH
        )
        ctx.audit.record(event)
        assert [e.id for e in ctx.audit.list_events()] == ["e1"]
    finally:
        ctx.close()
    ctx.close()  # idempotent


def test_open_context_file_db_shares_url_and_persists(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'cf.db'}"
    with open_context(AppConfig(db_url=url), clock=lambda: FIXED) as ctx:
        ctx.store.set_setting("k", "v")
        ctx.audit.record(
            AuditEvent(
                id="e2", at=FIXED, actor="a", actor_kind=ActorKind.AGENT, kind=AuditKind.ADMIN
            )
        )
    with open_context(AppConfig(db_url=url)) as again:
        assert again.store.get_setting("k") == "v"
        assert [e.id for e in again.audit.list_events()] == ["e2"]


def test_default_clock_is_aware_utc() -> None:
    with open_context(AppConfig(db_url="sqlite://")) as ctx:
        now = ctx.now()
    assert now.tzinfo is not None and now.utcoffset() == dt.timedelta(0)


def test_open_context_loads_profiles_from_connections_file(
    tmp_path: Path, warehouse_path: Path
) -> None:
    shutil.copy(warehouse_path, tmp_path / "warehouse.duckdb")
    conn = tmp_path / "connections.yml"
    conn.write_text(
        "profiles:\n"
        "  demo: {dialect: duckdb, options: {path: warehouse.duckdb}}\n"
        "  other: {dialect: postgres, options: {host: h}}\n"
    )
    config = AppConfig(db_url="sqlite://", connections_path=conn, default_profile="demo")
    with open_context(config) as ctx:
        assert ctx.warehouses.names() == ["demo", "other"]
        wh = ctx.warehouses.get()
        assert wh.dialect is Dialect.DUCKDB
        assert wh.run("SELECT COUNT(*) FROM deals", limit=1).rows[0][0] > 0
        assert ctx.warehouses.is_open("demo")
    assert not ctx.warehouses.is_open("demo")  # close() closed the warehouses


def test_open_context_profiles_override_and_bad_default() -> None:
    profiles = [WarehouseProfile(name="p", dialect=Dialect.POSTGRES)]
    with open_context(AppConfig(db_url="sqlite://"), profiles=profiles) as ctx:
        assert ctx.warehouses.default_name == "p"
    with pytest.raises(NotFoundError):
        open_context(AppConfig(db_url="sqlite://", default_profile="nope"), profiles=profiles)


def test_open_context_bad_connections_file(tmp_path: Path) -> None:
    conn = tmp_path / "c.yml"
    conn.write_text("profiles:\n  pg: {dialect: postgres, options: {password: x}}\n")
    with pytest.raises(CodecError):
        open_context(AppConfig(db_url="sqlite://", connections_path=conn))


def test_team_mode_default_set_and_fail_safe() -> None:
    with open_context(AppConfig(db_url="sqlite://")) as ctx:
        assert team_mode(ctx) is TeamMode.OPEN
        assert set_team_mode(ctx, TeamMode.STRICT) is TeamMode.STRICT
        assert team_mode(ctx) is TeamMode.STRICT
        set_team_mode(ctx, TeamMode.OPEN)
        assert team_mode(ctx) is TeamMode.OPEN
        ctx.store.set_setting(MODE_SETTING, "garbage")
        assert team_mode(ctx) is TeamMode.STRICT


@pytest.mark.parametrize(
    ("url", "shown"),
    [
        (
            "postgresql+psycopg://user:hunter2@host/db",
            "postgresql+psycopg://user:***@host/db",
        ),
        ("postgresql://u:p%40ss@db.example:5432/lib", "postgresql://u:***@db.example:5432/lib"),
        ("postgresql://u:pa/ss@h/db", "postgresql://u:***@h/db"),  # unencoded '/'
        (
            "postgresql://h/db?password=hunter2&sslmode=require",
            "postgresql://h/db?password=***&sslmode=require",
        ),
        ("postgresql://u@h/db", "postgresql://u@h/db"),
        ("sqlite:///cteforge.db", "sqlite:///cteforge.db"),
        ("sqlite://", "sqlite://"),
    ],
)
def test_redact_db_url(url: str, shown: str) -> None:
    assert redact_db_url(url) == shown
    assert AppConfig(db_url=url).display_db_url == shown
