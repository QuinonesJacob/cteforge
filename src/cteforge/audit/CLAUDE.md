# src/cteforge/audit/

I/O adapter: cteforge's own audit log, and the SQL comment tag (DESIGN §11, ADR 0008).
Project root: ../../../CLAUDE.md

## What belongs here (authoritative)
- `log.py` — `AuditLog`: writes/reads `AuditEvent`s (domain/audit.py) in its own
  `audit_events` table (SQLAlchemy Core), in the same database URL as the store.
- `tag.py` — compile ids and the SQL comment tag.

If something does not clearly fit one of the above, it does not belong here.
"Not forbidden" is not the same as "allowed."

## Public API
```python
class AuditLog:
    def __init__(self, url: str) -> None
    def init_schema(self) -> None                       # idempotent
    def record(self, event: AuditEvent) -> None         # raises AuditError on failure
    def list_events(self, *, limit: int = 100, actor: str | None = None,
                    kind: AuditKind | None = None, definition_id: str | None = None,
                    since: datetime | None = None) -> list[AuditEvent]   # newest first

def new_compile_id() -> str                             # 12 lowercase hex chars
def sql_tag(definitions: list[str], compile_id: str) -> str
    # ["realization_rate@3"] -> "/* cteforge: realization_rate v3 compile_id=8f2a… */"
    # Sanitizes: no "*/", no newlines, ids restricted to [a-z0-9_]; exploratory = "cteforge: exploratory".
def tag_sql(sql: str, tag: str) -> str                  # tag + "\n" + sql
```

## Rules
- Never store result rows, SQL text with values, credentials or tokens. `sql_sha256` only.
- `definition_id` filter matches any entry of `definitions` with that id prefix "<id>@".

## Common misplacements
- Warehouse query history — read from the warehouse via warehouse/, never copied here.
- Deciding what to log — services/.

## Interactions
Imports domain and SQLAlchemy only. Imported only by services.

## If you need to add something not on the allowlist
STOP. Do not place the file here on the assumption it kind of fits. Surface the case to
the user, propose the addition, and update this CLAUDE.md (and the root CLAUDE.md if the
change is project-wide) before placing the new code. The architecture evolves through
conscious decisions, not silent drift.
