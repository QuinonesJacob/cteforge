"""SQLAlchemy Core tables for the store (ADR 0016).

Portable types only (SQLite locally, Postgres on a server). Whole models are stored as text
(codec YAML for definitions, JSON for everything else); the other columns are indexes.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    true,
)

# 2: roles, tokens, users.active (ADR 0027). A version-1 database is migrated on open
# (Store.init_schema): each user's token_sha256 becomes a token named "default".
# 3: ADR 0026 — recipes became composed definitions (migrated on open; the recipe tables
# are kept, read only by that migration), pending recipe proposals withdrawn, removed tool
# names stripped from roles and tokens, join edges lose `state` (confirmed_by/added_by set
# from approved join proposals).
STORE_SCHEMA_VERSION = 3

_ID = 200  # Max length of ids and names used as keys.
_ENUM = 32  # Max length of enum values (state, status, role, kind).
_DISPLAY_NAME = 80  # domain.users.MAX_DISPLAY_NAME
_EMAIL = 254  # domain.users.MAX_EMAIL

metadata = MetaData()

schema_meta = Table(
    "schema_meta",
    metadata,
    Column("key", String(_ID), primary_key=True),
    Column("value", Text, nullable=False),
)

definitions = Table(
    "definitions",
    metadata,
    Column("id", String(_ID), primary_key=True),
    Column("latest_version", Integer, nullable=False),
    Column("state", String(_ENUM), nullable=False, index=True),  # of the latest version
    Column("title", Text, nullable=False),
    Column("keywords", Text, nullable=False),  # JSON list
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

definition_versions = Table(
    "definition_versions",
    metadata,
    Column("pk", Integer, primary_key=True, autoincrement=True),
    Column("definition_id", String(_ID), ForeignKey("definitions.id"), nullable=False),
    Column("version", Integer, nullable=False),
    Column("yaml", Text, nullable=False),  # domain/codec.py — the single format
    Column("state", String(_ENUM), nullable=False),
    Column("author", String(_ID), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("change_note", Text, nullable=False),
    Column("logic_changed", Boolean, nullable=False),
    Column("verified_by", String(_ID), nullable=True),
    Column("verified_at", Date, nullable=True),
    UniqueConstraint("definition_id", "version", name="uq_definition_versions_id_version"),
)

# Legacy (ADR 0026): read only by the schema-3 migration, which turns each recipe into a
# composed definition. Nothing writes them any more.
recipes = Table(
    "recipes",
    metadata,
    Column("id", String(_ID), primary_key=True),
    Column("latest_version", Integer, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

recipe_versions = Table(
    "recipe_versions",
    metadata,
    Column("pk", Integer, primary_key=True, autoincrement=True),
    Column("recipe_id", String(_ID), ForeignKey("recipes.id"), nullable=False),
    Column("version", Integer, nullable=False),
    Column("data", Text, nullable=False),  # Recipe JSON
    Column("author", String(_ID), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("recipe_id", "version", name="uq_recipe_versions_id_version"),
)

join_edges = Table(
    "join_edges",
    metadata,
    Column("id", String(_ID * 2), primary_key=True),
    Column("left_table", String(_ID), nullable=False, index=True),  # lowercased
    Column("right_table", String(_ID), nullable=False, index=True),  # lowercased
    Column("data", Text, nullable=False),  # JoinEdge JSON
)

proposals = Table(
    "proposals",
    metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),  # tie-break for ordering
    Column("id", String(64), nullable=False, unique=True),
    Column("kind", String(_ENUM), nullable=False),
    Column("status", String(_ENUM), nullable=False, index=True),
    Column("target_id", String(_ID * 2), nullable=False, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("data", Text, nullable=False),  # Proposal JSON
)

users = Table(
    "users",
    metadata,
    Column("name", String(_ID), primary_key=True),
    Column("role", String(_ENUM), nullable=False),  # a roles.name (not a foreign key)
    Column("kind", String(_ENUM), nullable=False),
    # Legacy (schema 1): no longer authenticates anything. Version-1 values were migrated
    # to `tokens`; new users get a random value nobody knows. Kept NOT NULL + unique so a
    # version-1 binary can't open the database (schema_version 2 refuses it anyway).
    Column("token_sha256", String(64), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    # Added to version-1 databases by an explicit ALTER (Store._migrate_v1).
    Column("active", Boolean, nullable=False, server_default=true()),
    # ADR 0029: optional profile. Added to older databases by an explicit ALTER.
    Column("display_name", String(_DISPLAY_NAME), nullable=True),
    Column("email", String(_EMAIL), nullable=True),
)

# Configurable roles (ADR 0027). Built-ins are seeded by init_schema when missing.
roles = Table(
    "roles",
    metadata,
    Column("name", String(_ENUM), primary_key=True),
    Column("description", Text, nullable=False),
    Column("allowed", Text, nullable=False),  # JSON list of tool names
    Column("defaults", Text, nullable=False),  # JSON list of tool names (⊆ allowed)
    Column("review_all", Boolean, nullable=False),
    Column("builtin", Boolean, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

# Personal access tokens (ADR 0027): many per user, stored only as SHA-256 hashes.
tokens = Table(
    "tokens",
    metadata,
    Column("id", String(64), primary_key=True),  # uuid4 hex
    Column("user_name", String(_ID), ForeignKey("users.name"), nullable=False, index=True),
    Column("name", String(_ID), nullable=False),
    Column("prefix", String(16), nullable=False),  # display only; "" for migrated tokens
    Column("token_sha256", String(64), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("created_by", String(_ID), nullable=False),
    Column("last_used_at", DateTime(timezone=True), nullable=True),
    Column("expires_at", DateTime(timezone=True), nullable=True),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
    Column("tool_mode", String(_ENUM), nullable=False),
    Column("include_tools", Text, nullable=False),  # JSON list of tool names
    Column("exclude_tools", Text, nullable=False),  # JSON list of tool names
    # The token that minted this one for its own user; revoking a token revokes its
    # descendants. Added to existing tables by an explicit ALTER (Store._ensure_columns).
    Column("parent_token_id", String(64), nullable=True, index=True),
)

settings = Table(
    "settings",
    metadata,
    Column("key", String(_ID), primary_key=True),
    Column("value", Text, nullable=False),
)

# Meaning search (ADR 0023): one vector per definition version per embedding model.
# Derived data: rebuilt from the definitions at any time (`cteforge search-index rebuild`).
search_vectors = Table(
    "search_vectors",
    metadata,
    Column("definition_id", String(_ID), primary_key=True),
    Column("version", Integer, primary_key=True),
    Column("model", String(_ID), primary_key=True),
    Column("dim", Integer, nullable=False),
    Column("doc_sha256", String(64), nullable=False),
    Column("vector", LargeBinary, nullable=False),  # little-endian float32 × dim
    Column("created_at", DateTime(timezone=True), nullable=False),
)

# Renamed definition IDs (ADR 0024): one row per ID a definition no longer has.
# `definition_id` is the CURRENT id (rewritten on each later rename, so a chain resolves in
# one lookup); `renamed_to` is what that rename made it. Created by `create_all`; no
# migration or schema version bump.
former_ids = Table(
    "former_ids",
    metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),  # rename order
    Column("former_id", String(_ID), nullable=False, unique=True),
    Column("definition_id", String(_ID), nullable=False, index=True),
    Column("renamed_to", String(_ID), nullable=False),
    Column("renamed_by", String(_ID), nullable=False),
    Column("renamed_at", DateTime(timezone=True), nullable=False),
    Column("proposal_id", String(64), nullable=True),
)
