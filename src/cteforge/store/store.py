"""The `Store`: database persistence for the library (ADR 0016). See store/CLAUDE.md.

SQLAlchemy 2 Core, portable SQL only. Whole models are serialized (codec YAML for
definitions, JSON for the rest); the store never enumerates model fields beyond the
indexed columns, so optional model fields can be added without touching this file.
"""

from __future__ import annotations

import datetime as dt
import json
import secrets
import struct
import threading
import uuid
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from typing import Any

from sqlalchemy import (
    Connection,
    Engine,
    Row,
    Table,
    and_,
    create_engine,
    delete,
    event,
    insert,
    inspect,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, IntegrityError, NoSuchModuleError, SQLAlchemyError
from sqlalchemy.pool import StaticPool

from cteforge.domain.access import BUILTIN_ROLES, Role, TokenInfo, Tool, ToolMode, sorted_tools
from cteforge.domain.codec import definition_from_yaml, definition_to_yaml
from cteforge.domain.errors import StaleVersionError, StoreError
from cteforge.domain.models import (
    Definition,
    DefinitionState,
    DefinitionVersionInfo,
    FormerId,
    JoinEdge,
    Recipe,
)
from cteforge.domain.proposals import Proposal, ProposalStatus, proposal_with_definition_renamed
from cteforge.domain.search import SearchVector
from cteforge.domain.users import ActorKind, User
from cteforge.store import tables as t


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _utc(value: dt.datetime) -> dt.datetime:
    """SQLite returns naive datetimes (stored as UTC); Postgres returns aware ones."""
    return value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value.astimezone(dt.UTC)


def _enable_sqlite_fks(dbapi_conn: Any, _record: Any) -> None:
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def _make_engine(url: str) -> Engine:
    try:
        parsed = make_url(url)
        if parsed.get_backend_name() != "sqlite":
            return create_engine(parsed, pool_pre_ping=True)
        args = {"check_same_thread": False}
        if parsed.database in (None, "", ":memory:"):
            engine = create_engine(parsed, connect_args=args, poolclass=StaticPool)
        else:
            engine = create_engine(parsed, connect_args=args)
    except (ArgumentError, NoSuchModuleError, ImportError) as exc:
        # Never echo the URL: it may carry a password.
        raise StoreError(f"Cannot open database ({type(exc).__name__})") from exc
    event.listen(engine, "connect", _enable_sqlite_fks)
    return engine


class _NewerSchema(StoreError):
    """The database was written by a newer cteforge."""


def _ensure_users_active(conn: Connection) -> None:
    """Version-1 `users` tables lack `active`: add it (idempotent; static DDL)."""
    columns = {c["name"] for c in inspect(conn).get_columns("users")}
    if "active" not in columns:
        conn.execute(text("ALTER TABLE users ADD COLUMN active BOOLEAN NOT NULL DEFAULT TRUE"))


def _migrate_v1_tokens(conn: Connection, now: dt.datetime) -> None:
    """ADR 0027: each user's single version-1 token hash becomes a token named
    "default" (follow_role) when the user has no token rows yet, so it keeps working; the
    legacy column then gets a random value nobody knows."""
    has_tokens = select(t.tokens.c.id).where(t.tokens.c.user_name == t.users.c.name).exists()
    rows = conn.execute(select(t.users.c.name, t.users.c.token_sha256).where(~has_tokens)).all()
    for row in rows:
        info = TokenInfo(
            id=uuid.uuid4().hex,
            user_name=row.name,
            name="default",
            prefix="",
            created_at=now,
            created_by=row.name,
        )
        conn.execute(insert(t.tokens).values(**_token_values(info), token_sha256=row.token_sha256))
        # The legacy column no longer authenticates; leave nothing usable in it.
        conn.execute(
            update(t.users)
            .where(t.users.c.name == row.name)
            .values(token_sha256=secrets.token_hex(32))
        )


def _seed_builtin_roles(conn: Connection, now: dt.datetime) -> None:
    """Insert each built-in role that is missing (edits to existing ones are kept)."""
    existing = set(conn.execute(select(t.roles.c.name)).scalars())
    for role in BUILTIN_ROLES.values():
        if role.name not in existing:
            conn.execute(insert(t.roles).values(**(_role_values(role) | {"updated_at": now})))


class _UpsertRace(StoreError):
    """An upsert's INSERT lost to a concurrent INSERT of the same key; retry it."""


def _expect_next(latest: int | None, version: int, what: str, id: str) -> None:
    expected = (latest or 0) + 1
    if version != expected:
        raise StaleVersionError(
            f"{what} '{id}' version {version} is stale: expected {expected} "
            f"(latest is {latest or 'none'})"
        )


class Store:
    """Persistence for definitions, recipes, joins, proposals, users, settings and search
    vectors."""

    def __init__(self, url: str) -> None:
        self._engine = _make_engine(url)
        # In-memory SQLite shares ONE DBAPI connection across threads (StaticPool);
        # serialize every use of it so transactions cannot interleave on it.
        shared = isinstance(self._engine.pool, StaticPool)
        self._lock: AbstractContextManager[Any] = threading.RLock() if shared else nullcontext()

    # ------------------------------------------------------------------ plumbing

    @contextmanager
    def _tx(self, conflict: StoreError | None = None) -> Iterator[Connection]:
        """One transaction per mutating call. IntegrityError → `conflict`."""
        try:
            with self._lock, self._engine.begin() as conn:
                yield conn
        except IntegrityError as exc:
            raise (conflict or StoreError("Write conflicts with existing data")) from exc
        except SQLAlchemyError as exc:
            raise StoreError(f"Database error ({type(exc).__name__})") from exc

    @contextmanager
    def _read(self) -> Iterator[Connection]:
        try:
            with self._lock, self._engine.connect() as conn:
                yield conn
        except SQLAlchemyError as exc:
            raise StoreError(f"Database error ({type(exc).__name__})") from exc

    def _upsert(self, table: Table, key: Any, values: dict[str, Any]) -> None:
        """UPDATE by `key`, INSERT if absent. Under READ COMMITTED two first-time upserts
        can both miss on UPDATE; the loser's INSERT violates the key, so retry once in a
        fresh transaction, where the UPDATE now finds the winner's row."""
        for conflict in (_UpsertRace(), None):
            try:
                with self._tx(conflict=conflict) as conn:
                    result = conn.execute(update(table).where(key).values(**values))
                    if result.rowcount == 0:
                        conn.execute(insert(table).values(**values))
                return
            except _UpsertRace:
                continue

    def close(self) -> None:
        """Dispose the engine's connections. In-memory SQLite data is gone afterwards."""
        with self._lock:
            self._engine.dispose()

    def init_schema(self) -> None:
        """Create missing tables, migrate a version-1 database (ADR 0027), seed the
        built-in roles. Idempotent; a concurrent first open by another process is retried
        once."""
        try:
            self._init_schema_once()
        except _NewerSchema:
            raise
        except StoreError:
            self._init_schema_once()

    def _init_schema_once(self) -> None:
        with self._tx() as conn:
            t.metadata.create_all(conn)
            row = conn.execute(
                select(t.schema_meta.c.value).where(t.schema_meta.c.key == "schema_version")
            ).first()
            if row is not None and int(row.value) > t.STORE_SCHEMA_VERSION:
                raise _NewerSchema(
                    f"Database schema version {row.value} is newer than this cteforge "
                    f"supports ({t.STORE_SCHEMA_VERSION}); upgrade cteforge"
                )
            _ensure_users_active(conn)
            if row is None:
                conn.execute(
                    insert(t.schema_meta).values(
                        key="schema_version", value=str(t.STORE_SCHEMA_VERSION)
                    )
                )
            elif int(row.value) < 2:
                _migrate_v1_tokens(conn, _now())
                conn.execute(
                    update(t.schema_meta)
                    .where(t.schema_meta.c.key == "schema_version")
                    .values(value=str(t.STORE_SCHEMA_VERSION))
                )
            _seed_builtin_roles(conn, _now())

    @staticmethod
    def _latest(conn: Connection, table: Table, id: str) -> int | None:
        return conn.execute(
            select(table.c.latest_version).where(table.c.id == id)
        ).scalar_one_or_none()

    @staticmethod
    def _advance(conn: Connection, table: Table, id: str, version: int, **values: Any) -> None:
        """Move the latest pointer to `version`; atomic against concurrent writers."""
        values["updated_at"] = _now()
        if version == 1:
            conn.execute(insert(table).values(id=id, latest_version=1, **values))
            return
        result = conn.execute(
            update(table)
            .where(table.c.id == id, table.c.latest_version == version - 1)
            .values(latest_version=version, **values)
        )
        if result.rowcount != 1:
            raise StaleVersionError(f"'{id}' moved on while version {version} was written")

    # --------------------------------------------------------------- definitions

    def add_definition_version(
        self,
        d: Definition,
        *,
        author: str,
        change_note: str = "",
        logic_changed: bool = True,
    ) -> Definition:
        stale = StaleVersionError(f"Definition '{d.id}' version {d.version} already exists")
        with self._tx(conflict=stale) as conn:
            _expect_next(self._latest(conn, t.definitions, d.id), d.version, "Definition", d.id)
            self._advance(
                conn,
                t.definitions,
                d.id,
                d.version,
                state=d.state.value,
                title=d.title,
                keywords=json.dumps(d.keywords),
            )
            conn.execute(
                insert(t.definition_versions).values(
                    definition_id=d.id,
                    version=d.version,
                    yaml=definition_to_yaml(d),
                    state=d.state.value,
                    author=author,
                    created_at=_now(),
                    change_note=change_note,
                    logic_changed=logic_changed,
                    verified_by=d.verified_by,
                    verified_at=d.verified_at,
                )
            )
        return d

    def _version_yaml(self, conn: Connection, id: str, version: int | None) -> str | None:
        dv = t.definition_versions
        if version is None:
            version = self._latest(conn, t.definitions, id)
            if version is None:
                return None
        return conn.execute(
            select(dv.c.yaml).where(dv.c.definition_id == id, dv.c.version == version)
        ).scalar_one_or_none()

    def get_definition(self, id: str, version: int | None = None) -> Definition | None:
        with self._read() as conn:
            text = self._version_yaml(conn, id, version)
        return None if text is None else definition_from_yaml(text)

    def latest_version(self, id: str) -> int | None:
        with self._read() as conn:
            return self._latest(conn, t.definitions, id)

    def list_definitions(self, states: set[DefinitionState] | None = None) -> list[Definition]:
        dv, defs = t.definition_versions, t.definitions
        query = (
            select(dv.c.yaml)
            .join(
                defs,
                and_(defs.c.id == dv.c.definition_id, defs.c.latest_version == dv.c.version),
            )
            .order_by(defs.c.id)
        )
        if states is not None:
            query = query.where(defs.c.state.in_([s.value for s in states]))
        with self._read() as conn:
            texts = conn.execute(query).scalars().all()
        return [definition_from_yaml(text) for text in texts]

    def definition_history(self, id: str) -> list[DefinitionVersionInfo]:
        dv = t.definition_versions
        query = select(dv).where(dv.c.definition_id == id).order_by(dv.c.version)
        with self._read() as conn:
            rows = conn.execute(query).all()
        return [_version_info(row) for row in rows]

    def set_definition_state(
        self,
        id: str,
        version: int,
        state: DefinitionState,
        *,
        verified_by: str | None = None,
        verified_at: dt.date | None = None,
    ) -> Definition:
        dv = t.definition_versions
        changes = {"state": state, "verified_by": verified_by, "verified_at": verified_at}
        with self._tx() as conn:
            text = self._version_yaml(conn, id, version)
            if text is None:
                raise StoreError(f"Definition '{id}' version {version} does not exist")
            current = definition_from_yaml(text)
            updated = Definition.model_validate(current.model_dump() | changes)
            conn.execute(
                update(dv)
                .where(dv.c.definition_id == id, dv.c.version == version)
                .values(yaml=definition_to_yaml(updated), **changes)
            )
            conn.execute(
                update(t.definitions)
                .where(t.definitions.c.id == id, t.definitions.c.latest_version == version)
                .values(state=state.value)
            )
        return updated

    # ------------------------------------------------------------ renaming (ADR 0024)

    def rename_definition(
        self,
        old: str,
        new: str,
        *,
        renamed_by: str,
        renamed_at: dt.datetime | None = None,
        proposal_id: str | None = None,
    ) -> Definition:
        """Give definition `old` the id `new`, in ONE transaction, everywhere it is stored:
        the `definitions` row, every version (row and the id inside its YAML; version
        numbers, authors and states unchanged), every recipe version that uses it, every
        proposal that targets or uses it (`proposal_with_definition_renamed`), and its
        search vectors (deleted: the search document names the id, so they are rebuilt).
        Records `old` as a former id of `new` (earlier former ids of `old` now point at
        `new`). Taking back one of its own former ids removes that record. Audit events are
        never rewritten (they are in another table). Returns the latest version.
        StoreError when `old` does not exist, `new` exists, or `new` is a former id of
        another definition."""
        defs, dv, fi = t.definitions, t.definition_versions, t.former_ids
        conflict = StoreError(f"Definition '{new}' already exists (renamed concurrently?)")
        with self._tx(conflict=conflict) as conn:
            row = conn.execute(select(defs).where(defs.c.id == old)).first()
            if row is None:
                raise StoreError(f"Definition '{old}' does not exist")
            if self._latest(conn, defs, new) is not None:
                raise StoreError(f"Definition '{new}' already exists")
            taken = conn.execute(
                select(fi.c.definition_id).where(fi.c.former_id == new)
            ).scalar_one_or_none()
            if taken is not None and taken != old:
                raise StoreError(f"'{new}' is a former id of definition '{taken}'")
            conn.execute(delete(fi).where(fi.c.former_id == new))
            now = _now()
            when = _utc(renamed_at) if renamed_at is not None else now
            conn.execute(insert(defs).values(**(row._asdict() | {"id": new, "updated_at": now})))
            versions = conn.execute(
                select(dv.c.pk, dv.c.yaml).where(dv.c.definition_id == old)
            ).all()
            for pk, text in versions:
                renamed = definition_from_yaml(text).model_copy(update={"id": new})
                conn.execute(
                    update(dv)
                    .where(dv.c.pk == pk)
                    .values(definition_id=new, yaml=definition_to_yaml(renamed))
                )
            conn.execute(delete(defs).where(defs.c.id == old))
            conn.execute(update(fi).where(fi.c.definition_id == old).values(definition_id=new))
            conn.execute(
                insert(fi).values(
                    former_id=old,
                    definition_id=new,
                    renamed_to=new,
                    renamed_by=renamed_by,
                    renamed_at=when,
                    proposal_id=proposal_id,
                )
            )
            rv = t.recipe_versions
            for pk, data in conn.execute(select(rv.c.pk, rv.c.data)).all():
                recipe = Recipe.model_validate_json(data)
                if recipe.uses_definition(old):
                    renamed_recipe = recipe.with_definition_renamed(old, new)
                    conn.execute(
                        update(rv)
                        .where(rv.c.pk == pk)
                        .values(data=renamed_recipe.model_dump_json())
                    )
            tp = t.proposals
            for seq, data in conn.execute(select(tp.c.seq, tp.c.data)).all():
                changed = proposal_with_definition_renamed(
                    Proposal.model_validate_json(data), old, new
                )
                if changed is not None:
                    conn.execute(
                        update(tp)
                        .where(tp.c.seq == seq)
                        .values(target_id=changed.target_id, data=changed.model_dump_json())
                    )
            sv = t.search_vectors
            conn.execute(delete(sv).where(sv.c.definition_id == old))
            text = self._version_yaml(conn, new, None)
        assert text is not None
        return definition_from_yaml(text)

    def former_id(self, id: str) -> FormerId | None:
        """The rename record of `id` when it is a former id (ADR 0024), else None."""
        with self._read() as conn:
            row = conn.execute(select(t.former_ids).where(t.former_ids.c.former_id == id)).first()
        return None if row is None else _former(row)

    def former_ids(self, definition_id: str | None = None) -> list[FormerId]:
        """Former ids (of `definition_id`, its current id; None = of every definition),
        oldest rename first."""
        fi = t.former_ids
        query = select(fi).order_by(fi.c.renamed_at, fi.c.seq)
        if definition_id is not None:
            query = query.where(fi.c.definition_id == definition_id)
        with self._read() as conn:
            rows = conn.execute(query).all()
        return [_former(row) for row in rows]

    # ------------------------------------------------------------------- recipes

    def add_recipe_version(self, r: Recipe, *, author: str) -> Recipe:
        stale = StaleVersionError(f"Recipe '{r.id}' version {r.version} already exists")
        with self._tx(conflict=stale) as conn:
            _expect_next(self._latest(conn, t.recipes, r.id), r.version, "Recipe", r.id)
            self._advance(conn, t.recipes, r.id, r.version)
            conn.execute(
                insert(t.recipe_versions).values(
                    recipe_id=r.id,
                    version=r.version,
                    data=r.model_dump_json(),
                    author=author,
                    created_at=_now(),
                )
            )
        return r

    def get_recipe(self, id: str, version: int | None = None) -> Recipe | None:
        rv = t.recipe_versions
        with self._read() as conn:
            if version is None:
                version = self._latest(conn, t.recipes, id)
            data = conn.execute(
                select(rv.c.data).where(rv.c.recipe_id == id, rv.c.version == version)
            ).scalar_one_or_none()
        return None if data is None else Recipe.model_validate_json(data)

    def list_recipes(self) -> list[Recipe]:
        rv, rec = t.recipe_versions, t.recipes
        query = (
            select(rv.c.data)
            .join(rec, and_(rec.c.id == rv.c.recipe_id, rec.c.latest_version == rv.c.version))
            .order_by(rec.c.id)
        )
        with self._read() as conn:
            rows = conn.execute(query).scalars().all()
        return [Recipe.model_validate_json(data) for data in rows]

    def recipes_using(self, definition_id: str) -> list[Recipe]:
        return [r for r in self.list_recipes() if r.uses_definition(definition_id)]

    # ------------------------------------------------------------------ join map

    def upsert_join(self, edge: JoinEdge) -> JoinEdge:
        values = {
            "id": edge.id,
            "left_table": edge.left_table.lower(),
            "right_table": edge.right_table.lower(),
            "data": edge.model_dump_json(),
        }
        self._upsert(t.join_edges, t.join_edges.c.id == edge.id, values)
        return edge

    def get_join(self, id: str) -> JoinEdge | None:
        with self._read() as conn:
            data = conn.execute(
                select(t.join_edges.c.data).where(t.join_edges.c.id == id)
            ).scalar_one_or_none()
        return None if data is None else JoinEdge.model_validate_json(data)

    def list_joins(self, table: str | None = None) -> list[JoinEdge]:
        je = t.join_edges
        query = select(je.c.data).order_by(je.c.id)
        if table is not None:
            name = table.lower()
            query = query.where(or_(je.c.left_table == name, je.c.right_table == name))
        with self._read() as conn:
            rows = conn.execute(query).scalars().all()
        return [JoinEdge.model_validate_json(data) for data in rows]

    # ----------------------------------------------------------------- proposals

    @staticmethod
    def _proposal_values(p: Proposal) -> dict[str, Any]:
        return {
            "kind": p.kind.value,
            "status": p.status.value,
            "target_id": p.target_id,
            "created_at": _utc(p.created_at),
            "data": p.model_dump_json(),
        }

    def add_proposal(self, p: Proposal) -> Proposal:
        with self._tx(conflict=StoreError(f"Proposal '{p.id}' already exists")) as conn:
            conn.execute(insert(t.proposals).values(id=p.id, **self._proposal_values(p)))
        return p

    def get_proposal(self, id: str) -> Proposal | None:
        with self._read() as conn:
            data = conn.execute(
                select(t.proposals.c.data).where(t.proposals.c.id == id)
            ).scalar_one_or_none()
        return None if data is None else Proposal.model_validate_json(data)

    def list_proposals(
        self, status: ProposalStatus | None = None, target_id: str | None = None
    ) -> list[Proposal]:
        tp = t.proposals
        query = select(tp.c.data).order_by(tp.c.created_at.desc(), tp.c.seq.desc())
        if status is not None:
            query = query.where(tp.c.status == status.value)
        if target_id is not None:
            query = query.where(tp.c.target_id == target_id)
        with self._read() as conn:
            rows = conn.execute(query).scalars().all()
        return [Proposal.model_validate_json(data) for data in rows]

    def update_proposal(
        self, p: Proposal, *, expected_status: ProposalStatus | None = None
    ) -> Proposal:
        """Replace the proposal by id. With `expected_status`, a compare-and-swap: the row
        is updated only while its stored status is still `expected_status` (one UPDATE
        ... WHERE status = ..., atomic across processes); otherwise StoreError."""
        tp = t.proposals
        query = update(tp).where(tp.c.id == p.id)
        if expected_status is not None:
            query = query.where(tp.c.status == expected_status.value)
        with self._tx() as conn:
            result = conn.execute(query.values(**self._proposal_values(p)))
            if result.rowcount == 1:
                return p
            current = conn.execute(select(tp.c.status).where(tp.c.id == p.id)).scalar_one_or_none()
        if current is None:
            raise StoreError(f"Proposal '{p.id}' does not exist")
        assert expected_status is not None
        raise StoreError(
            f"Proposal '{p.id}' is {current}, not {expected_status.value}; "
            "it was changed concurrently"
        )

    # --------------------------------------------------------------------- users

    def add_user(self, user: User) -> User:
        """Store a new user (no token: tokens are added with `add_token`). The legacy
        `users.token_sha256` column gets a random value nobody knows (ADR 0027)."""
        created = User(
            name=user.name,
            role=user.role,
            kind=user.kind,
            active=user.active,
            created_at=_utc(user.created_at or _now()),
        )
        conflict = StoreError(f"User '{user.name}' already exists")
        with self._tx(conflict=conflict) as conn:
            conn.execute(
                insert(t.users).values(
                    name=created.name,
                    role=created.role,
                    kind=created.kind.value,
                    token_sha256=secrets.token_hex(32),  # never shown, never matched
                    created_at=created.created_at,
                    active=created.active,
                )
            )
        return created

    def get_user(self, name: str) -> User | None:
        with self._read() as conn:
            row = conn.execute(select(t.users).where(t.users.c.name == name)).first()
        return None if row is None else _user(row)

    def list_users(self) -> list[User]:
        with self._read() as conn:
            rows = conn.execute(select(t.users).order_by(t.users.c.name)).all()
        return [_user(row) for row in rows]

    def _update_user(self, name: str, **values: Any) -> User:
        with self._tx() as conn:
            result = conn.execute(update(t.users).where(t.users.c.name == name).values(**values))
            if result.rowcount != 1:
                raise StoreError(f"User '{name}' does not exist")
            row = conn.execute(select(t.users).where(t.users.c.name == name)).one()
        return _user(row)

    def set_user_role(self, name: str, role: str) -> User:
        return self._update_user(name, role=str(role))

    def set_user_active(self, name: str, active: bool) -> User:
        return self._update_user(name, active=active)

    # --------------------------------------------------------------------- roles

    def list_roles(self) -> list[Role]:
        """Built-in roles first (viewer, contributor, approver, admin), then by name."""
        with self._read() as conn:
            rows = conn.execute(select(t.roles)).all()
        order = list(BUILTIN_ROLES)
        roles = [_role(row) for row in rows]
        return sorted(
            roles, key=lambda r: (order.index(r.name) if r.name in order else len(order), r.name)
        )

    def get_role(self, name: str) -> Role | None:
        with self._read() as conn:
            row = conn.execute(select(t.roles).where(t.roles.c.name == name)).first()
        return None if row is None else _role(row)

    def add_role(self, role: Role) -> Role:
        conflict = StoreError(f"Role '{role.name}' already exists")
        with self._tx(conflict=conflict) as conn:
            conn.execute(insert(t.roles).values(**_role_values(role)))
        return role

    def update_role(self, role: Role) -> Role:
        """Replace `role`'s description, allowed, defaults and review_all (its `builtin`
        flag is kept as stored). Missing → StoreError."""
        values = _role_values(role)
        values.pop("builtin")
        with self._tx() as conn:
            result = conn.execute(
                update(t.roles).where(t.roles.c.name == role.name).values(**values)
            )
            if result.rowcount != 1:
                raise StoreError(f"Role '{role.name}' does not exist")
            row = conn.execute(select(t.roles).where(t.roles.c.name == role.name)).one()
        return _role(row)

    def delete_role(self, name: str) -> None:
        """Delete an unused role. Missing, or held by any user → StoreError (checked in
        the same transaction)."""
        with self._tx() as conn:
            in_use = conn.execute(
                select(t.users.c.name).where(t.users.c.role == name).limit(1)
            ).first()
            if in_use is not None:
                raise StoreError(f"Role '{name}' is held by users; give them another role first")
            result = conn.execute(delete(t.roles).where(t.roles.c.name == name))
            if result.rowcount != 1:
                raise StoreError(f"Role '{name}' does not exist")

    # -------------------------------------------------------------------- tokens

    def add_token(self, info: TokenInfo, token_sha256: str) -> TokenInfo:
        """Store a token's metadata and hash (the store never sees a raw token). Unknown
        user, duplicate id or hash → StoreError."""
        conflict = StoreError("Token could not be stored (unknown user or duplicate token)")
        with self._tx(conflict=conflict) as conn:
            exists = conn.execute(
                select(t.users.c.name).where(t.users.c.name == info.user_name)
            ).first()
            if exists is None:
                raise StoreError(f"User '{info.user_name}' does not exist")
            conn.execute(insert(t.tokens).values(**_token_values(info), token_sha256=token_sha256))
        return self._token_or_error(info.id)

    def get_token(self, id: str) -> TokenInfo | None:
        with self._read() as conn:
            row = conn.execute(select(*_TOKEN_COLUMNS).where(t.tokens.c.id == id)).first()
        return None if row is None else _token(row)

    def _token_or_error(self, id: str) -> TokenInfo:
        found = self.get_token(id)
        if found is None:
            raise StoreError(f"Token '{id}' does not exist")
        return found

    def list_tokens(self, user_name: str | None = None) -> list[TokenInfo]:
        """Tokens (revoked and expired included), oldest first; of one user when given."""
        query = select(*_TOKEN_COLUMNS).order_by(t.tokens.c.created_at, t.tokens.c.id)
        if user_name is not None:
            query = query.where(t.tokens.c.user_name == user_name)
        with self._read() as conn:
            rows = conn.execute(query).all()
        return [_token(row) for row in rows]

    def revoke_token(self, id: str, at: dt.datetime) -> TokenInfo:
        """Mark revoked at `at` (an already revoked token keeps its first time)."""
        with self._tx() as conn:
            found = conn.execute(select(t.tokens.c.id).where(t.tokens.c.id == id)).first()
            if found is None:
                raise StoreError(f"Token '{id}' does not exist")
            conn.execute(
                update(t.tokens)
                .where(t.tokens.c.id == id, t.tokens.c.revoked_at.is_(None))
                .values(revoked_at=_utc(at))
            )
        return self._token_or_error(id)

    def set_token_tools(
        self, id: str, mode: ToolMode, include: frozenset[Tool], exclude: frozenset[Tool]
    ) -> TokenInfo:
        with self._tx() as conn:
            result = conn.execute(
                update(t.tokens)
                .where(t.tokens.c.id == id)
                .values(
                    tool_mode=ToolMode(mode).value,
                    include_tools=_tools_json(include),
                    exclude_tools=_tools_json(exclude),
                )
            )
            if result.rowcount != 1:
                raise StoreError(f"Token '{id}' does not exist")
        return self._token_or_error(id)

    def user_by_token_hash(
        self, token_sha256: str, now: dt.datetime
    ) -> tuple[User, TokenInfo] | None:
        """The user and token for a token hash, or None when the hash is unknown, the
        token is revoked or expired at `now`, or the user is deactivated."""
        now = _utc(now)
        tk, us = t.tokens, t.users
        query = (
            select(
                *_TOKEN_COLUMNS,
                us.c.role.label("u_role"),
                us.c.kind.label("u_kind"),
                us.c.active.label("u_active"),
                us.c.created_at.label("u_created_at"),
            )
            .join(us, us.c.name == tk.c.user_name)
            .where(
                tk.c.token_sha256 == token_sha256,
                tk.c.revoked_at.is_(None),
                or_(tk.c.expires_at.is_(None), tk.c.expires_at > now),
                us.c.active.is_(True),
            )
        )
        with self._read() as conn:
            row = conn.execute(query).first()
        if row is None:
            return None
        token = _token(row)
        if not token.usable_at(now) or not row.u_active:  # belt and braces
            return None
        user = User(
            name=token.user_name,
            role=row.u_role,
            kind=ActorKind(row.u_kind),
            active=bool(row.u_active),
            created_at=_utc(row.u_created_at),
        )
        return user, token

    def touch_token(
        self, id: str, at: dt.datetime, *, min_interval: dt.timedelta = dt.timedelta(minutes=1)
    ) -> bool:
        """Set last_used_at = `at` unless it was set less than `min_interval` before (one
        conditional UPDATE). True when written."""
        at = _utc(at)
        tk = t.tokens
        with self._tx() as conn:
            result = conn.execute(
                update(tk)
                .where(
                    tk.c.id == id,
                    or_(tk.c.last_used_at.is_(None), tk.c.last_used_at <= at - min_interval),
                )
                .values(last_used_at=at)
            )
        return result.rowcount == 1

    # ------------------------------------------------------------------ settings

    def get_setting(self, key: str) -> str | None:
        with self._read() as conn:
            return conn.execute(
                select(t.settings.c.value).where(t.settings.c.key == key)
            ).scalar_one_or_none()

    def set_setting(self, key: str, value: str) -> None:
        self._upsert(t.settings, t.settings.c.key == key, {"key": key, "value": value})

    def delete_setting(self, key: str) -> None:
        """Remove a setting (absent → no-op), so `get_setting` returns None again."""
        with self._tx() as conn:
            conn.execute(delete(t.settings).where(t.settings.c.key == key))

    # ------------------------------------------------------------- search vectors

    def put_search_vector(self, v: SearchVector) -> None:
        """Insert or replace the vector of (definition_id, version, model)."""
        sv = t.search_vectors
        key = and_(
            sv.c.definition_id == v.definition_id,
            sv.c.version == v.version,
            sv.c.model == v.model,
        )
        values = {
            "definition_id": v.definition_id,
            "version": v.version,
            "model": v.model,
            "dim": v.dim,
            "doc_sha256": v.doc_sha256,
            "vector": _pack(v.vector),
            "created_at": _now(),
        }
        self._upsert(sv, key, values)

    def search_vectors(self, model: str) -> list[SearchVector]:
        """Every stored vector of `model`, ordered by definition id and version."""
        sv = t.search_vectors
        query = select(sv).where(sv.c.model == model).order_by(sv.c.definition_id, sv.c.version)
        with self._read() as conn:
            rows = conn.execute(query).all()
        return [
            SearchVector(
                definition_id=row.definition_id,
                version=row.version,
                model=row.model,
                doc_sha256=row.doc_sha256,
                vector=_unpack(row.vector, row.dim),
            )
            for row in rows
        ]

    def search_vector_models(self) -> list[str]:
        """The models that have stored vectors, sorted."""
        sv = t.search_vectors
        with self._read() as conn:
            return list(conn.execute(select(sv.c.model).distinct().order_by(sv.c.model)).scalars())

    def delete_search_vectors(self, model: str | None = None) -> int:
        """Delete the vectors of `model` (None: of every model). Returns how many."""
        sv = t.search_vectors
        query = delete(sv) if model is None else delete(sv).where(sv.c.model == model)
        with self._tx() as conn:
            return conn.execute(query).rowcount


def _pack(vector: tuple[float, ...]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def _unpack(blob: bytes, dim: int) -> tuple[float, ...]:
    if len(blob) != 4 * dim:
        raise StoreError("Stored search vector has the wrong size")
    return struct.unpack(f"<{dim}f", blob)


def _version_info(row: Row[Any]) -> DefinitionVersionInfo:
    return DefinitionVersionInfo(
        id=row.definition_id,
        version=row.version,
        state=DefinitionState(row.state),
        author=row.author,
        created_at=_utc(row.created_at),
        change_note=row.change_note,
        logic_changed=row.logic_changed,
        verified_by=row.verified_by,
        verified_at=row.verified_at,
    )


def _former(row: Row[Any]) -> FormerId:
    return FormerId(
        former_id=row.former_id,
        definition_id=row.definition_id,
        renamed_to=row.renamed_to,
        renamed_by=row.renamed_by,
        renamed_at=_utc(row.renamed_at),
        proposal_id=row.proposal_id,
    )


def _user(row: Row[Any]) -> User:
    return User(
        name=row.name,
        role=row.role,
        kind=ActorKind(row.kind),
        active=bool(row.active),
        created_at=_utc(row.created_at),
    )


def _tools_json(tools: frozenset[Tool] | set[Tool]) -> str:
    return json.dumps([tool.value for tool in sorted_tools(frozenset(tools))])


def _tools_from(raw: str) -> frozenset[Tool]:
    """Stored tool names → Tools; names this version does not know are dropped (a newer
    or older catalog never grants an unknown tool)."""
    known = {tool.value for tool in Tool}
    return frozenset(Tool(name) for name in json.loads(raw) if name in known)


def _role_values(role: Role) -> dict[str, Any]:
    return {
        "name": role.name,
        "description": role.description,
        "allowed": _tools_json(role.allowed),
        "defaults": _tools_json(role.defaults),
        "review_all": role.review_all,
        "builtin": role.builtin,
        "updated_at": _now(),
    }


def _role(row: Row[Any]) -> Role:
    allowed = _tools_from(row.allowed)
    return Role(
        name=row.name,
        description=row.description,
        allowed=allowed,
        defaults=_tools_from(row.defaults) & allowed,
        review_all=bool(row.review_all),
        builtin=bool(row.builtin),
    )


# Every tokens column except the hash: reads never return it.
_TOKEN_COLUMNS = tuple(c for c in t.tokens.c if c.name != "token_sha256")


def _token_values(info: TokenInfo) -> dict[str, Any]:
    return {
        "id": info.id,
        "user_name": info.user_name,
        "name": info.name,
        "prefix": info.prefix,
        "created_at": _utc(info.created_at),
        "created_by": info.created_by,
        "last_used_at": _utc(info.last_used_at) if info.last_used_at else None,
        "expires_at": _utc(info.expires_at) if info.expires_at else None,
        "revoked_at": _utc(info.revoked_at) if info.revoked_at else None,
        "tool_mode": info.tool_mode.value,
        "include_tools": _tools_json(info.include),
        "exclude_tools": _tools_json(info.exclude),
    }


def _token(row: Row[Any]) -> TokenInfo:
    def when(value: dt.datetime | None) -> dt.datetime | None:
        return _utc(value) if value is not None else None

    return TokenInfo(
        id=row.id,
        user_name=row.user_name,
        name=row.name,
        prefix=row.prefix,
        created_at=_utc(row.created_at),
        created_by=row.created_by,
        last_used_at=when(row.last_used_at),
        expires_at=when(row.expires_at),
        revoked_at=when(row.revoked_at),
        tool_mode=ToolMode(row.tool_mode),
        include=_tools_from(row.include_tools),
        exclude=_tools_from(row.exclude_tools),
    )
