from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import CodecError, NotFoundError, PolicyError, WarehouseError
from cteforge.files.connections import load_profiles, profiles_from_yaml
from cteforge.services.connections import (
    OPENERS,
    WarehouseRegistry,
    open_profile,
    open_warehouse,
)

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"


# ── files/connections.py ────────────────────────────────────────────────────────────


def test_load_profiles_reads_every_field(tmp_path: Path) -> None:
    path = tmp_path / "connections.yml"
    path.write_text(
        """
profiles:
  demo:
    dialect: duckdb
    options: {path: wh.duckdb}
  pg:
    dialect: postgres
    options: {host: db.internal, port: 5432, dbname: analytics, user: ro}
    secrets_env: {password: PG_PASSWORD}
    max_rows: 50
  bq:
    dialect: bigquery
    options: {project: p}
    maximum_bytes_billed: 1000
"""
    )
    demo, pg, bq = load_profiles(path)
    assert demo.name == "demo" and demo.dialect is Dialect.DUCKDB
    # Relative DuckDB paths resolve next to the connections file.
    assert demo.options["path"] == str((tmp_path / "wh.duckdb").resolve())
    assert pg.options == {"host": "db.internal", "port": 5432, "dbname": "analytics", "user": "ro"}
    assert pg.secrets_env == {"password": "PG_PASSWORD"}
    assert pg.max_rows == 50
    assert bq.maximum_bytes_billed == 1000 and bq.max_rows == 10_000


def test_absolute_duckdb_path_is_kept(tmp_path: Path) -> None:
    target = tmp_path / "abs.duckdb"
    (profile,) = profiles_from_yaml(
        f"profiles:\n  d:\n    dialect: duckdb\n    options: {{path: '{target}'}}\n",
        base_dir="/elsewhere",
    )
    assert profile.options["path"] == str(target)


def test_empty_file_means_no_profiles(tmp_path: Path) -> None:
    path = tmp_path / "c.yml"
    path.write_text("")
    assert load_profiles(path) == []
    assert profiles_from_yaml("profiles: {}\n") == []


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "PG_PASSWORD",
        "token",
        "access_token",
        "private_key",
        "credentials",
        "credentials_json",
        "client_secret",
        "api_key",
        "passphrase",
        "dsn",
        "conninfo",
    ],
)
def test_inline_secret_options_are_refused_without_echoing_value(key: str) -> None:
    text = f"profiles:\n  pg:\n    dialect: postgres\n    options:\n      {key}: hunter2-value\n"
    with pytest.raises(CodecError) as info:
        profiles_from_yaml(text)
    message = str(info.value)
    assert "secrets_env" in message
    assert key in message
    assert "hunter2-value" not in message


def test_credentials_file_path_option_is_allowed() -> None:
    (profile,) = profiles_from_yaml(
        "profiles:\n  bq:\n    dialect: bigquery\n    options: {credentials_file: /k.json}\n"
    )
    assert profile.options["credentials_file"] == "/k.json"


def test_url_with_embedded_password_is_refused() -> None:
    text = (
        "profiles:\n  pg:\n    dialect: postgres\n    options: {host: 'postgres://u:s3cr3t@db/x'}\n"
    )
    with pytest.raises(CodecError) as info:
        profiles_from_yaml(text)
    assert "s3cr3t" not in str(info.value)
    assert "host" in str(info.value)


def test_secret_value_pasted_into_secrets_env_is_refused() -> None:
    text = "profiles:\n  pg:\n    dialect: postgres\n    secrets_env: {password: 'p@ss w0rd!'}\n"
    with pytest.raises(CodecError) as info:
        profiles_from_yaml(text)
    assert "p@ss w0rd!" not in str(info.value)
    assert "password" in str(info.value)


def test_validation_errors_do_not_echo_values() -> None:
    text = "profiles:\n  pg:\n    dialect: postgres\n    options: {host: [a-secret-list]}\n"
    with pytest.raises(CodecError) as info:
        profiles_from_yaml(text)
    assert "a-secret-list" not in str(info.value)
    assert "options" in str(info.value)


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("- a\n- b\n", "single 'profiles' key"),
        ("profiles: {}\nextra: 1\n", "single 'profiles' key"),
        ("profiles: [a]\n", "map profile names"),
        ("profiles:\n  x: duckdb\n", "must be a mapping"),
        ("profiles:\n  x: {dialect: oracle}\n", "dialect"),
        ("profiles:\n  x: {dialect: duckdb, colour: red}\n", "unknown keys"),
        ("profiles:\n  'bad name': {dialect: duckdb}\n", "Invalid profile name"),
        ("profiles:\n  x: {dialect: duckdb}\n  x: {dialect: postgres}\n", "Duplicate key 'x'"),
        ("profiles: [unclosed\n", "not valid YAML"),
    ],
)
def test_malformed_files_raise_codec_error(text: str, fragment: str) -> None:
    with pytest.raises(CodecError, match=None) as info:
        profiles_from_yaml(text)
    assert fragment in str(info.value)


def test_missing_file_raises_codec_error(tmp_path: Path) -> None:
    with pytest.raises(CodecError):
        load_profiles(tmp_path / "nope.yml")


def test_example_connections_file_loads() -> None:
    (demo,) = load_profiles(EXAMPLES / "connections.example.yml")
    assert demo.dialect is Dialect.DUCKDB
    assert demo.options["path"] == str((EXAMPLES / "warehouse.duckdb").resolve())


# ── services/connections.py ─────────────────────────────────────────────────────────


class FakeWarehouse:
    def __init__(self, profile: WarehouseProfile) -> None:
        self.profile = profile
        self.dialect = profile.dialect
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


def _profile(name: str, dialect: Dialect = Dialect.POSTGRES, **kw: Any) -> WarehouseProfile:
    return WarehouseProfile(name=name, dialect=dialect, **kw)


def _registry(
    *profiles: WarehouseProfile, default: str | None = None
) -> tuple[WarehouseRegistry, list[FakeWarehouse]]:
    opened: list[FakeWarehouse] = []

    def opener(profile: WarehouseProfile) -> FakeWarehouse:
        wh = FakeWarehouse(profile)
        opened.append(wh)
        return wh

    return WarehouseRegistry(profiles, default=default, opener=opener), opened  # type: ignore[arg-type]


def test_registry_opens_lazily_and_caches() -> None:
    reg, opened = _registry(_profile("a"), _profile("b", Dialect.SNOWFLAKE))
    assert opened == []
    assert reg.names() == ["a", "b"]
    first = reg.get("a")
    assert reg.get("a") is first
    assert [w.profile.name for w in opened] == ["a"]
    assert reg.is_open("a") and not reg.is_open("b")


def test_registry_default_resolution() -> None:
    single, _ = _registry(_profile("only"))
    assert single.default_name == "only"
    assert single.get().profile.name == "only"  # type: ignore[attr-defined]

    several, _ = _registry(_profile("a"), _profile("b"))
    assert several.default_name is None
    with pytest.raises(PolicyError, match="CTEFORGE_DEFAULT_PROFILE"):
        several.get()

    chosen, _ = _registry(_profile("a"), _profile("b"), default="b")
    assert chosen.get().profile.name == "b"  # type: ignore[attr-defined]
    assert chosen.profile().name == "b"


def test_empty_registry_gives_clear_policy_error() -> None:
    reg, _ = _registry()
    assert reg.names() == [] and reg.summaries() == []
    with pytest.raises(PolicyError, match="CTEFORGE_CONNECTIONS"):
        reg.get()


def test_unknown_profile_and_default() -> None:
    reg, _ = _registry(_profile("a"))
    with pytest.raises(NotFoundError, match="'zzz'"):
        reg.get("zzz")
    with pytest.raises(NotFoundError, match="'zzz'"):
        reg.profile("zzz")
    with pytest.raises(NotFoundError):
        _registry(_profile("a"), default="zzz")


def test_duplicate_profile_names_rejected() -> None:
    with pytest.raises(CodecError):
        _registry(_profile("a"), _profile("a"))


def test_summaries_never_include_options_or_secrets() -> None:
    reg, _ = _registry(
        _profile("pg", options={"host": "db.internal"}, secrets_env={"password": "PG_PW"})
    )
    (summary,) = reg.summaries()
    assert summary.model_dump(mode="json") == {"name": "pg", "dialect": "postgres"}
    assert reg.summary("pg") == summary


def test_close_all_closes_each_once_and_continues_after_failure() -> None:
    reg, opened = _registry(_profile("a"), _profile("b"))
    reg.get("a")
    reg.get("b")

    def boom() -> None:
        raise WarehouseError("close failed")

    opened[0].close = boom  # type: ignore[method-assign]
    reg.close_all()
    assert opened[1].closed == 1
    reg.close_all()  # nothing left open
    assert opened[1].closed == 1
    assert not reg.is_open("a")
    # Re-opening after close_all gives a fresh warehouse.
    assert reg.get("b") is not opened[1]


def test_failed_open_is_not_cached() -> None:
    calls = 0

    def opener(profile: WarehouseProfile) -> FakeWarehouse:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise WarehouseError("down")
        return FakeWarehouse(profile)

    reg = WarehouseRegistry([_profile("a")], opener=opener)  # type: ignore[arg-type]
    with pytest.raises(WarehouseError):
        reg.get("a")
    assert reg.get("a").dialect is Dialect.POSTGRES
    assert calls == 2


def test_every_dialect_has_an_opener() -> None:
    assert set(OPENERS) == set(Dialect)


def test_duckdb_profile_opens_real_warehouse(warehouse_path: Path) -> None:
    reg = WarehouseRegistry(
        [_profile("demo", Dialect.DUCKDB, options={"path": str(warehouse_path)}, max_rows=3)]
    )
    wh = reg.get()
    assert wh.dialect is Dialect.DUCKDB
    result = wh.run("SELECT deal_id FROM deals ORDER BY deal_id", limit=100)
    assert len(result.rows) == 3 and result.truncated
    reg.close_all()


def test_open_profile_missing_driver_names_install_command(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    real = importlib.import_module

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "psycopg":
            raise ImportError(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    with pytest.raises(WarehouseError, match=r"cteforge\[postgres\]"):
        open_profile(_profile("pg"))


def test_duckdb_profile_without_path_errors() -> None:
    with pytest.raises(WarehouseError, match="options.path"):
        open_profile(_profile("d", Dialect.DUCKDB))


def test_open_warehouse_m1_still_works(warehouse_path: Path) -> None:
    wh, dialect = open_warehouse(warehouse_path)
    assert dialect is Dialect.DUCKDB
    assert wh.column_types("deals")
    wh.close()


# ── SerializedWarehouse: bounded waits ───────────────────────────────────────────────


class _SlowWarehouse:
    dialect = Dialect.DUCKDB

    def __init__(self) -> None:
        import threading

        self.started = threading.Event()
        self.release = threading.Event()
        self.closed = False

    def run(self, sql: str, limit: int) -> Any:
        self.started.set()
        self.release.wait(5)
        return "done"

    def explain(self, sql: str) -> None:
        return None

    def column_types(self, table: str) -> dict[str, str]:
        return {}

    def close(self) -> None:
        self.closed = True


def test_a_long_query_makes_other_callers_fail_fast_not_queue() -> None:
    """One slow query must not hold every other caller's worker thread indefinitely."""
    import threading
    import time

    from cteforge.services.connections import SerializedWarehouse

    slow = _SlowWarehouse()
    wh = SerializedWarehouse(slow, busy_timeout=0.05, close_timeout=0.05)  # type: ignore[arg-type]
    runner = threading.Thread(target=wh.run, args=("SELECT 1", 1))
    runner.start()
    try:
        assert slow.started.wait(2)
        started = time.perf_counter()
        with pytest.raises(WarehouseError, match="busy"):
            wh.explain("SELECT 1")
        with pytest.raises(WarehouseError, match="busy"):
            wh.column_types("deals")
        assert time.perf_counter() - started < 1
        wh.close()  # returns instead of hanging; the running call keeps its connection
        assert not slow.closed
    finally:
        slow.release.set()
        runner.join(5)
    wh.close()
    assert slow.closed
    wh.explain("SELECT 1")  # free again once the slow call is done


def test_registry_passes_its_busy_timeout_on() -> None:
    reg = WarehouseRegistry([_profile("a")], opener=lambda p: FakeWarehouse(p), busy_timeout=0.5)  # type: ignore[arg-type,return-value]
    assert reg.get("a")._busy_timeout == 0.5  # type: ignore[attr-defined]
