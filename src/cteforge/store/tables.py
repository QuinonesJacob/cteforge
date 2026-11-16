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
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

STORE_SCHEMA_VERSION = 1

_ID = 200  # Max length of ids and names used as keys.
_ENUM = 32  # Max length of enum values (state, status, role, kind).

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
    Column("role", String(_ENUM), nullable=False),
    Column("kind", String(_ENUM), nullable=False),
    Column("token_sha256", String(64), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

settings = Table(
    "settings",
    metadata,
    Column("key", String(_ID), primary_key=True),
    Column("value", Text, nullable=False),
)
