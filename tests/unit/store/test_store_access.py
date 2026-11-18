"""Roles, personal access tokens, users.active and the version-1 migration (ADR 0027)."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import (
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    insert,
    inspect,
    select,
    update,
)

from cteforge.domain.access import BUILTIN_ROLES, Role, TokenInfo, Tool, ToolMode
from cteforge.domain.errors import StoreError
from cteforge.domain.users import ActorKind, User, UserRole
from cteforge.store import Store
from cteforge.store import tables as t

T0 = dt.datetime(2026, 11, 16, 9, 0, tzinfo=dt.UTC)


def token(id: str, user: str = "ann", **extra: object) -> TokenInfo:
    data: dict[str, object] = {
        "id": id,
        "user_name": user,
        "name": f"token {id}",
        "prefix": "cfg_pat_abcd",
        "created_at": T0,
        "created_by": user,
    }
    return TokenInfo(**(data | extra))


@pytest.fixture
def ann(store: Store) -> User:
    return store.add_user(User(name="ann", role=UserRole.VIEWER, created_at=T0))


# ── roles ────────────────────────────────────────────────────────────────────────────


def test_builtin_roles_are_seeded_in_order(store: Store) -> None:
    roles = store.list_roles()
    assert [r.name for r in roles] == ["viewer", "contributor", "approver", "admin"]
    assert all(r.builtin for r in roles)
    assert store.get_role("approver") == BUILTIN_ROLES["approver"]


def test_role_edits_survive_reopening(store: Store, file_url: str) -> None:
    s = Store(file_url)
    s.init_schema()
    narrower = BUILTIN_ROLES["viewer"].model_copy(
        update={"defaults": frozenset({Tool.SEARCH}), "description": "edited"}
    )
    s.update_role(narrower)
    s.close()
    again = Store(file_url)
    again.init_schema()
    assert again.get_role("viewer").defaults == {Tool.SEARCH}
    assert again.get_role("viewer").description == "edited"


def test_custom_role_crud(store: Store, ann: User) -> None:
    role = Role(
        name="support_bot",
        description="search and run",
        allowed=frozenset({Tool.SEARCH, Tool.COMPILE_AND_RUN}),
        defaults=frozenset({Tool.SEARCH}),
    )
    store.add_role(role)
    assert store.get_role("support_bot") == role
    assert [r.name for r in store.list_roles()][-1] == "support_bot"
    with pytest.raises(StoreError):
        store.add_role(role)
    edited = store.update_role(
        role.model_copy(update={"review_all": True, "builtin": True})  # builtin is kept
    )
    assert edited.review_all is True and edited.builtin is False
    store.set_user_role("ann", "support_bot")
    with pytest.raises(StoreError, match="held by users"):
        store.delete_role("support_bot")
    store.set_user_role("ann", "viewer")
    store.delete_role("support_bot")
    assert store.get_role("support_bot") is None
    with pytest.raises(StoreError):
        store.delete_role("support_bot")
    with pytest.raises(StoreError):
        store.update_role(role)


def test_unknown_stored_tool_names_are_dropped(store: Store) -> None:
    store.add_role(Role(name="odd", allowed=frozenset({Tool.SEARCH})))
    with store._tx() as conn:  # noqa: SLF001 - simulate a newer catalog's data
        conn.execute(
            update(t.roles)
            .where(t.roles.c.name == "odd")
            .values(allowed=json.dumps(["search", "teleport"]), defaults='["teleport"]')
        )
    assert store.get_role("odd").allowed == {Tool.SEARCH}
    assert store.get_role("odd").defaults == frozenset()


# ── tokens ───────────────────────────────────────────────────────────────────────────


def test_add_get_list_tokens_never_return_the_hash(store: Store, ann: User) -> None:
    store.add_user(User(name="bo", role=UserRole.ADMIN))
    first = store.add_token(token("a", include=frozenset({Tool.SEARCH})), "a" * 64)
    store.add_token(token("b", created_at=T0 + dt.timedelta(minutes=1)), "b" * 64)
    store.add_token(token("c", user="bo"), "c" * 64)
    assert first == token("a", include=frozenset({Tool.SEARCH}))
    assert "token_sha256" not in TokenInfo.model_fields
    assert [x.id for x in store.list_tokens("ann")] == ["a", "b"]
    assert {x.id for x in store.list_tokens()} == {"a", "b", "c"}
    assert store.get_token("zzz") is None


def test_token_conflicts(store: Store, ann: User) -> None:
    store.add_token(token("a"), "a" * 64)
    with pytest.raises(StoreError):
        store.add_token(token("b"), "a" * 64)  # same hash
    with pytest.raises(StoreError):
        store.add_token(token("a"), "b" * 64)  # same id
    with pytest.raises(StoreError, match="does not exist"):
        store.add_token(token("x", user="ghost"), "x" * 64)


def test_user_by_token_hash(store: Store, ann: User) -> None:
    store.add_token(token("a"), "a" * 64)
    user, info = store.user_by_token_hash("a" * 64, T0)
    assert user.name == "ann" and user.role == UserRole.VIEWER and user.kind is ActorKind.HUMAN
    assert info.id == "a"
    assert store.user_by_token_hash("f" * 64, T0) is None


def test_revoked_expired_inactive_do_not_authenticate(store: Store, ann: User) -> None:
    store.add_token(token("r"), "r" * 64)
    store.add_token(token("e", expires_at=T0 + dt.timedelta(hours=1)), "e" * 64)
    store.add_token(token("g"), "g" * 64)
    revoked = store.revoke_token("r", T0)
    assert revoked.revoked_at == T0
    assert store.revoke_token("r", T0 + dt.timedelta(days=1)).revoked_at == T0  # first time
    assert store.user_by_token_hash("r" * 64, T0) is None
    assert store.user_by_token_hash("e" * 64, T0 + dt.timedelta(minutes=59)) is not None
    assert store.user_by_token_hash("e" * 64, T0 + dt.timedelta(hours=1)) is None
    assert store.user_by_token_hash("g" * 64, T0) is not None
    store.set_user_active("ann", False)
    assert store.user_by_token_hash("g" * 64, T0) is None
    with pytest.raises(StoreError):
        store.revoke_token("nope", T0)


def test_touch_token_is_throttled(store: Store, ann: User) -> None:
    store.add_token(token("a"), "a" * 64)
    assert store.touch_token("a", T0) is True
    assert store.touch_token("a", T0 + dt.timedelta(seconds=59)) is False
    assert store.get_token("a").last_used_at == T0
    assert store.touch_token("a", T0 + dt.timedelta(minutes=1)) is True
    assert store.get_token("a").last_used_at == T0 + dt.timedelta(minutes=1)


def test_set_token_tools(store: Store, ann: User) -> None:
    store.add_token(token("a"), "a" * 64)
    info = store.set_token_tools(
        "a", ToolMode.ONLY, frozenset({Tool.SEARCH}), frozenset({Tool.CHECK_AND_RUN})
    )
    assert info.tool_mode is ToolMode.ONLY
    assert info.include == {Tool.SEARCH} and info.exclude == {Tool.CHECK_AND_RUN}
    with pytest.raises(StoreError):
        store.set_token_tools("nope", ToolMode.ONLY, frozenset(), frozenset())


def test_new_users_get_a_random_legacy_hash_that_matches_no_token(store: Store) -> None:
    store.add_user(User(name="a", role=UserRole.VIEWER))
    store.add_user(User(name="b", role=UserRole.VIEWER))
    with store._read() as conn:  # noqa: SLF001
        hashes = list(conn.execute(select(t.users.c.token_sha256)).scalars())
    assert len(set(hashes)) == 2 and all(len(h) == 64 for h in hashes)
    for h in hashes:
        assert store.user_by_token_hash(h, T0) is None
    assert store.list_tokens() == []


# ── migration from schema version 1 ──────────────────────────────────────────────────


def _v1_database(path: Path, users: list[tuple[str, str, str, str]]) -> str:
    """A database as cteforge with STORE_SCHEMA_VERSION 1 wrote it: users with one token
    hash each, no active column, no roles/tokens tables."""
    url = f"sqlite:///{path}"
    engine = create_engine(url)
    meta = MetaData()
    schema_meta = Table(
        "schema_meta",
        meta,
        Column("key", String(200), primary_key=True),
        Column("value", Text, nullable=False),
    )
    old_users = Table(
        "users",
        meta,
        Column("name", String(200), primary_key=True),
        Column("role", String(32), nullable=False),
        Column("kind", String(32), nullable=False),
        Column("token_sha256", String(64), nullable=False, unique=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )
    with engine.begin() as conn:
        meta.create_all(conn)
        conn.execute(insert(schema_meta).values(key="schema_version", value="1"))
        for name, role, kind, raw in users:
            conn.execute(
                insert(old_users).values(
                    name=name,
                    role=role,
                    kind=kind,
                    token_sha256=hashlib.sha256(raw.encode()).hexdigest(),
                    created_at=T0,
                )
            )
    engine.dispose()
    return url


def test_v1_tokens_migrate_to_default_tokens(tmp_path: Path) -> None:
    url = _v1_database(
        tmp_path / "old.db",
        [("root", "admin", "human", "root-secret"), ("bot", "contributor", "agent", "bot-secret")],
    )
    store = Store(url)
    store.init_schema()
    found = store.user_by_token_hash(hashlib.sha256(b"bot-secret").hexdigest(), T0)
    assert found is not None
    user, info = found
    assert (user.name, user.kind, user.active) == ("bot", ActorKind.AGENT, True)
    assert (info.name, info.prefix, info.tool_mode) == ("default", "", ToolMode.FOLLOW_ROLE)
    assert info.include == frozenset() and info.exclude == frozenset()
    assert store.user_by_token_hash(hashlib.sha256(b"root-secret").hexdigest(), T0)
    assert store.get_user("root").active is True
    with store._read() as conn:  # noqa: SLF001
        version = conn.execute(
            select(t.schema_meta.c.value).where(t.schema_meta.c.key == "schema_version")
        ).scalar_one()
        columns = {c["name"] for c in inspect(conn).get_columns("users")}
    assert version == str(t.STORE_SCHEMA_VERSION) and "active" in columns
    assert {"display_name", "email"} <= columns  # ADR 0029, added by ALTER
    assert store.get_user("root").display_name is None
    updated = store.set_user_profile("root", "Root", "root@example.com")
    assert (updated.display_name, updated.email) == ("Root", "root@example.com")
    assert store.user_by_token_hash(hashlib.sha256(b"root-secret").hexdigest(), T0)[0].email == (
        "root@example.com"
    )
    with store._read() as conn:  # noqa: SLF001
        legacy = set(conn.execute(select(t.users.c.token_sha256)).scalars())
    assert hashlib.sha256(b"bot-secret").hexdigest() not in legacy  # scrubbed
    assert [r.name for r in store.list_roles()] == ["viewer", "contributor", "approver", "admin"]
    store.close()

    # Re-opening migrates nothing twice, and a revoked default token stays revoked.
    again = Store(url)
    again.init_schema()
    [default] = again.list_tokens("bot")
    again.revoke_token(default.id, T0)
    again.close()
    third = Store(url)
    third.init_schema()
    assert len(third.list_tokens("bot")) == 1
    assert third.user_by_token_hash(hashlib.sha256(b"bot-secret").hexdigest(), T0) is None


def test_users_created_after_migration_get_no_phantom_token(tmp_path: Path) -> None:
    url = _v1_database(tmp_path / "old.db", [("root", "admin", "human", "x")])
    store = Store(url)
    store.init_schema()
    store.add_user(User(name="new", role=UserRole.VIEWER))
    store.close()
    again = Store(url)
    again.init_schema()
    assert again.list_tokens("new") == []


def test_newer_schema_is_refused(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'db.db'}"
    store = Store(url)
    store.init_schema()
    with store._tx() as conn:  # noqa: SLF001
        conn.execute(
            update(t.schema_meta)
            .where(t.schema_meta.c.key == "schema_version")
            .values(value=str(t.STORE_SCHEMA_VERSION + 1))
        )
    with pytest.raises(StoreError, match="newer"):
        Store(url).init_schema()


def test_revoking_a_token_revokes_its_descendants(store: Store, ann: User) -> None:
    store.add_token(token("p"), "p" * 64)
    store.add_token(token("c", parent_token_id="p"), "c" * 64)
    store.add_token(token("g", parent_token_id="c"), "g" * 64)
    store.add_token(token("s"), "s" * 64)  # unrelated sibling
    assert store.get_token("c").parent_token_id == "p"
    store.revoke_token("c", T0)
    assert store.get_token("p").revoked_at is None
    assert store.get_token("g").revoked_at == T0
    store.revoke_token("p", T0 + dt.timedelta(hours=1))
    assert store.get_token("p").revoked_at == T0 + dt.timedelta(hours=1)
    assert store.get_token("c").revoked_at == T0  # first time kept
    assert store.get_token("s").revoked_at is None
    for h in ("p", "c", "g"):
        assert store.user_by_token_hash(h * 64, T0 + dt.timedelta(hours=2)) is None


def test_v2_tokens_table_gains_parent_token_id(tmp_path: Path) -> None:
    """A version-2 database written before `parent_token_id` existed gets the column."""
    url = f"sqlite:///{tmp_path / 'v2.db'}"
    engine = create_engine(url)
    with engine.begin() as conn:
        t.metadata.create_all(conn)
        conn.exec_driver_sql("DROP INDEX ix_tokens_parent_token_id")
        conn.exec_driver_sql("ALTER TABLE tokens DROP COLUMN parent_token_id")
        conn.execute(insert(t.schema_meta).values(key="schema_version", value="2"))
        conn.execute(
            insert(t.users).values(
                name="ann",
                role="viewer",
                kind="human",
                token_sha256="0" * 64,
                created_at=T0,
                active=True,
            )
        )
        conn.exec_driver_sql(
            "INSERT INTO tokens (id, user_name, name, prefix, token_sha256, created_at, "
            "created_by, tool_mode, include_tools, exclude_tools) VALUES "
            f"('old', 'ann', 'default', '', '{'a' * 64}', '2026-11-16 09:00:00', 'ann', "
            "'follow_role', '[]', '[]')"
        )
        columns = {c["name"] for c in inspect(conn).get_columns("tokens")}
        assert "parent_token_id" not in columns
    engine.dispose()
    store = Store(url)
    store.init_schema()
    store.init_schema()  # idempotent
    assert store.get_token("old").parent_token_id is None
    store.add_token(token("new", parent_token_id="old"), "b" * 64)
    store.revoke_token("old", T0)
    assert store.get_token("new").revoked_at == T0
    with store._read() as conn:  # noqa: SLF001
        assert "parent_token_id" in {c["name"] for c in inspect(conn).get_columns("tokens")}
