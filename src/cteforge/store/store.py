"""The `Store`: database persistence for the library (ADR 0016). See store/CLAUDE.md.

SQLAlchemy 2 Core, portable SQL only. Whole models are serialized (codec YAML for
definitions, JSON for the rest); the store never enumerates model fields beyond the
indexed columns, so optional model fields can be added without touching this file.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
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
    event,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, IntegrityError, NoSuchModuleError, SQLAlchemyError
from sqlalchemy.pool import StaticPool

from cteforge.domain.codec import definition_from_yaml, definition_to_yaml
from cteforge.domain.errors import StaleVersionError, StoreError
from cteforge.domain.models import (
    Definition,
    DefinitionState,
    DefinitionVersionInfo,
    JoinEdge,
    Recipe,
)
from cteforge.domain.proposals import Proposal, ProposalStatus
from cteforge.domain.users import ActorKind, User, UserRole
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
    """Persistence for definitions, recipes, joins, proposals, users and settings."""

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
        with self._tx() as conn:
            t.metadata.create_all(conn)
            row = conn.execute(
                select(t.schema_meta.c.value).where(t.schema_meta.c.key == "schema_version")
            ).first()
            if row is None:
                conn.execute(
                    insert(t.schema_meta).values(
                        key="schema_version", value=str(t.STORE_SCHEMA_VERSION)
                    )
                )
            elif int(row.value) > t.STORE_SCHEMA_VERSION:
                raise StoreError(
                    f"Database schema version {row.value} is newer than this cteforge "
                    f"supports ({t.STORE_SCHEMA_VERSION}); upgrade cteforge"
                )

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
        return [
            r
            for r in self.list_recipes()
            if any(ref.id == definition_id for ref in r.uses.values())
        ]

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

    def add_user(self, user: User, token_sha256: str) -> User:
        created = User.model_validate(
            user.model_dump() | {"created_at": _utc(user.created_at or _now())}
        )
        conflict = StoreError(f"User '{user.name}' or this token already exists")
        with self._tx(conflict=conflict) as conn:
            conn.execute(
                insert(t.users).values(
                    name=created.name,
                    role=created.role.value,
                    kind=created.kind.value,
                    token_sha256=token_sha256,
                    created_at=created.created_at,
                )
            )
        return created

    def _one_user(self, *where: Any) -> User | None:
        with self._read() as conn:
            row = conn.execute(select(t.users).where(*where)).first()
        return None if row is None else _user(row)

    def get_user(self, name: str) -> User | None:
        return self._one_user(t.users.c.name == name)

    def user_by_token(self, token_sha256: str) -> User | None:
        return self._one_user(t.users.c.token_sha256 == token_sha256)

    def list_users(self) -> list[User]:
        with self._read() as conn:
            rows = conn.execute(select(t.users).order_by(t.users.c.name)).all()
        return [_user(row) for row in rows]

    def _update_user(self, name: str, conflict: StoreError | None = None, **values: Any) -> None:
        with self._tx(conflict=conflict) as conn:
            result = conn.execute(update(t.users).where(t.users.c.name == name).values(**values))
            if result.rowcount != 1:
                raise StoreError(f"User '{name}' does not exist")

    def set_user_role(self, name: str, role: UserRole) -> User:
        self._update_user(name, role=role.value)
        user = self.get_user(name)
        if user is None:  # Deleted between the two calls.
            raise StoreError(f"User '{name}' does not exist")
        return user

    def set_user_token(self, name: str, token_sha256: str) -> None:
        conflict = StoreError("This token is already assigned to another user")
        self._update_user(name, conflict=conflict, token_sha256=token_sha256)

    # ------------------------------------------------------------------ settings

    def get_setting(self, key: str) -> str | None:
        with self._read() as conn:
            return conn.execute(
                select(t.settings.c.value).where(t.settings.c.key == key)
            ).scalar_one_or_none()

    def set_setting(self, key: str, value: str) -> None:
        self._upsert(t.settings, t.settings.c.key == key, {"key": key, "value": value})


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


def _user(row: Row[Any]) -> User:
    return User(
        name=row.name,
        role=UserRole(row.role),
        kind=ActorKind(row.kind),
        created_at=_utc(row.created_at),
    )
