# src/cteforge/store/

I/O adapter: database persistence (SQLite locally, Postgres on a server). ADR 0016.
Project root: ../../../CLAUDE.md

## What belongs here (authoritative)
- `tables.py` — SQLAlchemy Core `MetaData` + tables: `schema_meta`, `definitions` (latest
  pointer + indexed columns), `definition_versions` (codec YAML text per version + author,
  created_at, change_note, logic_changed, state, verified_by/at), `recipes`/`recipe_versions`
  (JSON), `join_edges` (JSON + indexed tables), `proposals` (JSON + indexed status/target),
  `users` (name, role = a role name, kind, `active`, legacy token_sha256 unique — a
  random never-matched value for new users and after migration; ADR 0027), `roles` (name PK, description,
  allowed/defaults JSON tool lists, review_all, builtin, updated_at), `tokens` (id PK,
  user_name FK, name, prefix, token_sha256 unique, created_at/by, last_used_at,
  expires_at, revoked_at, tool_mode, include_tools/exclude_tools JSON), `settings`
  (key/value),
  `search_vectors` (PK definition_id + version + model; dim, doc_sha256, vector as
  little-endian float32 bytes, created_at — derived data for meaning search, ADR 0023;
  created by `create_all`, no schema version bump), `former_ids` (ADR 0024: seq PK,
  former_id unique, definition_id = the CURRENT id, renamed_to, renamed_by, renamed_at,
  proposal_id; `create_all`, no migration).
- `store.py` — the `Store` class (public API below). Nothing else is imported by services.

Schema version (`tables.STORE_SCHEMA_VERSION`) is 2. `init_schema` runs `create_all`, adds
`users.active` to an older table (explicit, idempotent `ALTER TABLE`, static DDL), on a
version-1 database migrates each user's `token_sha256` to a `default` token (follow_role)
for users with no token rows, then bumps the version; seeds missing built-in roles
(`domain.access.BUILTIN_ROLES`, edits kept); refuses a newer version. A failed first open
(concurrent process) is retried once.

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
    # renaming (ADR 0024) — one transaction: definitions row, every version (+ id in its
    # YAML), every recipe version using it, every proposal naming it
    # (domain.proposals.proposal_with_definition_renamed), search vectors deleted, former id
    # recorded (earlier former ids re-pointed; taking back an own former id removes it)
    def rename_definition(self, old: str, new: str, *, renamed_by: str,
                          renamed_at: datetime | None = None,
                          proposal_id: str | None = None) -> Definition   # latest version
        # StoreError: old missing, new exists, new is another definition's former id
    def former_id(self, id: str) -> FormerId | None
    def former_ids(self, definition_id: str | None = None) -> list[FormerId]  # oldest first
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
    def update_proposal(self, p: Proposal, *,
                        expected_status: ProposalStatus | None = None) -> Proposal
        # replace by id; with expected_status a compare-and-swap (UPDATE ... WHERE status =
        # expected): a row whose stored status differs → StoreError (missing → StoreError)
    # users (never stored with tools/review_all)
    def add_user(self, user: User) -> User                                # duplicate name -> StoreError
    def get_user(self, name: str) -> User | None
    def list_users(self) -> list[User]                                    # by name
    def set_user_role(self, name: str, role: str) -> User                 # a role name
    def set_user_active(self, name: str, active: bool) -> User
    # roles (ADR 0027); unknown stored tool names are dropped on read
    def list_roles(self) -> list[Role]                                    # built-ins first, then by name
    def get_role(self, name: str) -> Role | None
    def add_role(self, role: Role) -> Role                                # duplicate -> StoreError
    def update_role(self, role: Role) -> Role                             # builtin flag kept; missing -> StoreError
    def delete_role(self, name: str) -> None                              # held by a user / missing -> StoreError
    # tokens (hashes arrive already hashed; reads never return the hash)
    def add_token(self, info: TokenInfo, token_sha256: str) -> TokenInfo  # unknown user / dup -> StoreError
    def get_token(self, id: str) -> TokenInfo | None
    def list_tokens(self, user_name: str | None = None) -> list[TokenInfo]   # oldest first, revoked included
    def revoke_token(self, id: str, at: datetime) -> TokenInfo            # first revoked_at kept
    def set_token_tools(self, id, mode, include, exclude) -> TokenInfo
    def user_by_token_hash(self, token_sha256: str, now: datetime) -> tuple[User, TokenInfo] | None
        # None: unknown hash, revoked, expired at `now`, or user inactive (checked in SQL and again in Python)
    def touch_token(self, id: str, at: datetime, *, min_interval=1 minute) -> bool
        # conditional UPDATE: last_used_at written at most once per min_interval
    # settings
    def get_setting(self, key: str) -> str | None
    def set_setting(self, key: str, value: str) -> None
    def delete_setting(self, key: str) -> None                            # absent → no-op
    # search vectors (derived data; ADR 0023)
    def put_search_vector(self, v: SearchVector) -> None                  # upsert by (id, version, model)
    def search_vectors(self, model: str) -> list[SearchVector]            # by id, version
    def search_vector_models(self) -> list[str]
    def delete_search_vectors(self, model: str | None = None) -> int      # None = every model
```
Errors: `StoreError`, `StaleVersionError` from domain/errors.py. Missing rows return None
(getters) or raise StoreError (mutators).

Concurrency: a Store is safe to share across threads. In-memory SQLite ("sqlite://") uses
one shared connection, so every call is serialized by a lock. Across processes on one
database, `update_proposal(..., expected_status=...)` is the compare-and-swap services use
to decide a proposal at most once. Upserts (`upsert_join`,
`set_setting`, `put_search_vector`) retry once in a fresh transaction when a concurrent first-time INSERT of
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
