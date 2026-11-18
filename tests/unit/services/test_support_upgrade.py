"""services/_upgrade.py (ADR 0026): the store migration is audited when a context opens,
and the join map is re-mined once (sources), so a migrated recipe runs as a composed
definition."""

from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path

from sqlalchemy import create_engine, insert, update

from cteforge.domain.audit import AuditKind
from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.dialects import Dialect
from cteforge.domain.models import DefinitionState, TrustLabel
from cteforge.domain.users import ActorKind, User, UserRole
from cteforge.files.definitions import load_definition
from cteforge.services import _upgrade
from cteforge.services.context import AppConfig, open_context
from cteforge.services.run import compile_and_run
from cteforge.store import Store
from cteforge.store import tables as t

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"
NOW = dt.datetime(2026, 11, 1, tzinfo=dt.UTC)
AGENT = User(name="bot", role=UserRole.VIEWER, kind=ActorKind.AGENT)


def _old_database(path: Path) -> str:
    url = f"sqlite:///{path}"
    store = Store(url)
    store.init_schema()
    rr = load_definition(EXAMPLES / "definitions" / "realization_rate.yml")
    store.add_definition_version(
        rr.model_copy(update={"state": DefinitionState.VERIFIED}), author="ann"
    )
    store.close()
    recipe = {
        "schema_version": 1,
        "id": "apac_rr",
        "version": 1,
        "title": "APAC realization",
        "uses": {"rr": {"id": "realization_rate"}},
        "values": {"rr": {"regions": ["APAC"]}},
        "select": None,
        "dialect": "duckdb",
        "state": "llm_annotated",
    }
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(insert(t.recipes).values(id="apac_rr", latest_version=1, updated_at=NOW))
        conn.execute(
            insert(t.recipe_versions).values(
                recipe_id="apac_rr", version=1, data=json.dumps(recipe), author="x", created_at=NOW
            )
        )
        conn.execute(t.settings.delete().where(t.settings.c.key == _upgrade.JOIN_MAP_SETTING))
        conn.execute(
            update(t.schema_meta).where(t.schema_meta.c.key == "schema_version").values(value="2")
        )
    engine.dispose()
    return url


def test_migration_is_audited_and_the_migrated_recipe_runs(
    tmp_path: Path, warehouse_path: Path
) -> None:
    url = _old_database(tmp_path / "old.db")
    wh = tmp_path / "wh.duckdb"
    shutil.copy(warehouse_path, wh)
    profile = WarehouseProfile(name="demo", dialect=Dialect.DUCKDB, options={"path": str(wh)})
    with open_context(AppConfig(db_url=url), profiles=[profile]) as ctx:
        (event,) = ctx.audit.list_events(kind=AuditKind.ADMIN)
        assert event.actor == "cteforge" and event.extra["op"] == "migrate_adr_0026"
        assert event.definitions == ["apac_rr@1"]
        assert ctx.store.migration_notes() is None
        # Re-mined once: the definition's edges are known, with their sources.
        edges = ctx.store.list_joins()
        assert edges and all(e.sources == ["apac_rr", "realization_rate"] for e in edges)
        assert ctx.store.get_setting(_upgrade.JOIN_MAP_SETTING) == _upgrade.JOIN_MAP_VERSION
        out = compile_and_run(ctx, AGENT, uses={"r": {"id": "apac_rr"}})
        assert out.ran and {row[1] for row in out.rows} == {"APAC"}
        assert out.label is TrustLabel.BUILT_FROM_VERIFIED
    with open_context(AppConfig(db_url=url), profiles=[profile]) as again:
        assert len(again.audit.list_events(kind=AuditKind.ADMIN)) == 1  # once
