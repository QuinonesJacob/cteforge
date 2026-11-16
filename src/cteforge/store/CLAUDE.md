# src/cteforge/store/

I/O adapter: database persistence (SQLite locally, Postgres on a server). ADR 0016.
Project root: ../../../CLAUDE.md

## What belongs here (authoritative)
- `tables.py` — SQLAlchemy Core `MetaData` + tables: `schema_meta`, `definitions` (latest
  pointer + indexed columns), `definition_versions` (codec YAML text per version + author,
  created_at, change_note, logic_changed, state, verified_by/at), `recipes`/`recipe_versions`
  (JSON), `join_edges` (JSON + indexed tables), `proposals` (JSON + indexed status/target),
  `users` (name, role, kind, token_sha256 unique), `settings` (key/value).
- `store.py` — the `Store` class (public API below). Nothing else is imported by services.

If something does not clearly fit one of the above, it does not belong here.
"Not forbidden" is not the same as "allowed."

## Public API (services code against exactly this)
```python
class Store:
    def __init__(self, url: str) -> None          # "sqlite:///path.db", "sqlite://" (memory), "postgresql+psycopg://..."
    def init_schema(self) -> None                 # idempotent
    def close(self) -> None                       # dispose the engine (memory DB is lost)
    # definitions — stored via domain/codec.py YAML, the single format
    def add_definition_version(self, d: Definition, *, author: str, change_note: str = "",
                               logic_changed: bool = True) -> Definition
        # d.version must equal latest+1 (1 if new) else StaleVersionError
    def get_definition(self, id: str, version: int | None = None) -> Definition | None  # None = latest
    def latest_version(self, id: str) -> int | None
    def list_definitions(self, states: set[DefinitionState] | None = None) -> list[Definition]  # latest of each
    def definition_history(self, id: str) -> list[DefinitionVersionInfo]   # oldest first
    def set_definition_state(self, id: str, version: int, state: DefinitionState, *,
                             verified_by: str | None = None, verified_at: date | None = None) -> Definition
    # recipes
    def add_recipe_version(self, r: Recipe, *, author: str) -> Recipe     # same version rule
    def get_recipe(self, id: str, version: int | None = None) -> Recipe | None
    def list_recipes(self) -> list[Recipe]
    def recipes_using(self, definition_id: str) -> list[Recipe]           # latest versions; impact preview
    # join map
    def upsert_join(self, edge: JoinEdge) -> JoinEdge                     # replace by id
    def get_join(self, id: str) -> JoinEdge | None
    def list_joins(self, table: str | None = None) -> list[JoinEdge]      # table matches left or right
    # proposals
    def add_proposal(self, p: Proposal) -> Proposal
    def get_proposal(self, id: str) -> Proposal | None
    def list_proposals(self, status: ProposalStatus | None = None,
                       target_id: str | None = None) -> list[Proposal]    # newest first
    def update_proposal(self, p: Proposal) -> Proposal                    # replace by id
    # users (tokens arrive already hashed; the store never sees a raw token)
    def add_user(self, user: User, token_sha256: str) -> User             # duplicate name -> StoreError
    def get_user(self, name: str) -> User | None
    def user_by_token(self, token_sha256: str) -> User | None
    def list_users(self) -> list[User]
    def set_user_role(self, name: str, role: UserRole) -> User
    def set_user_token(self, name: str, token_sha256: str) -> None
    # settings
    def get_setting(self, key: str) -> str | None
    def set_setting(self, key: str, value: str) -> None
```
Errors: `StoreError`, `StaleVersionError` from domain/errors.py. Missing rows return None
(getters) or raise StoreError (mutators).

Concurrency: a Store is safe to share across threads. In-memory SQLite ("sqlite://") uses
one shared connection, so every call is serialized by a lock. Upserts (`upsert_join`,
`set_setting`) retry once in a fresh transaction when a concurrent first-time INSERT of
the same key wins (READ COMMITTED on Postgres).

## Common misplacements
- Definition serialization — reuse domain/codec.py.
- Approval rules, permissions, auto-apply — services/. Trust labels — trust/.
- Audit events — audit/ (own table, same database).

## Interactions
Imports domain and SQLAlchemy only. Imported only by services. Must not import other adapters.

## If you need to add something not on the allowlist
STOP. Do not place the file here on the assumption it kind of fits. Surface the case to
the user, propose the addition, and update this CLAUDE.md (and the root CLAUDE.md if the
change is project-wide) before placing the new code. The architecture evolves through
conscious decisions, not silent drift.
