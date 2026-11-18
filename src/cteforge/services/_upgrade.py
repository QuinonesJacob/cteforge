"""Data upgrades run once when a context opens (support module; `context.open_context`).

- Audit the store's schema migration (ADR 0026: recipes → composed definitions, pending
  recipe proposals withdrawn, removed tools stripped from roles and tokens): the store
  leaves a note (`Store.migration_notes`) in the migration's transaction; it is written
  to the audit log as an `admin` event (`op=migrate_adr_0026`, actor `cteforge`) and then
  cleared. Fail closed: if the event can't be written, opening fails and the note stays
  for the next open.
- Re-mine the join map once per database (setting `join_map_version` < 2): `sources` of
  every edge (ADR 0026: mined edges are known) and the edges of composed definitions.
"""

from __future__ import annotations

from cteforge.domain.audit import AuditKind
from cteforge.domain.users import ActorKind, User
from cteforge.services import _audit, _compose, _library
from cteforge.services.context import AppContext

JOIN_MAP_SETTING = "join_map_version"
JOIN_MAP_VERSION = "2"
# The actor of events cteforge records on its own (no person or agent asked for them).
SYSTEM_USER = User(name="cteforge", role="admin", kind=ActorKind.AGENT)


def after_open(ctx: AppContext) -> None:
    notes = ctx.store.migration_notes()
    if notes is not None:
        definitions = [
            f"{n['definition']}@1" for n in notes.get("recipes", []) if "definition" in n
        ]
        _audit.record(
            ctx,
            SYSTEM_USER,
            AuditKind.ADMIN,
            definitions=definitions,
            extra=notes | {"op": "migrate_adr_0026"},
        )
        ctx.store.clear_migration_notes()
    if ctx.store.get_setting(JOIN_MAP_SETTING) != JOIN_MAP_VERSION:
        _library.refresh_join_map(ctx, lambda d: _compose.compiled_sql(ctx, d))
        ctx.store.set_setting(JOIN_MAP_SETTING, JOIN_MAP_VERSION)
