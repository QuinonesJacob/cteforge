"""Warehouse connection profiles (ADR 0020). Holds NO secrets: secrets are referenced by
environment-variable name and resolved only inside warehouse/ at connect time."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from cteforge.domain.dialects import Dialect


class WarehouseProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    dialect: Dialect
    # Non-secret connection options, e.g. host, port, database, user, account, warehouse,
    # role, project, location, http_path, path (DuckDB file).
    options: dict[str, str | int | bool] = Field(default_factory=dict)
    # Driver parameter -> environment variable holding the secret, e.g.
    # {"password": "PG_PASSWORD"} or {"access_token": "DATABRICKS_TOKEN"}.
    secrets_env: dict[str, str] = Field(default_factory=dict)
    max_rows: int = 10_000
    maximum_bytes_billed: int | None = None  # BigQuery dry-run/run cap.
    # Per-statement timeout in seconds, enforced by each connector where the driver
    # supports it (warehouse/); None turns it off. A timed-out statement raises
    # WarehouseError("query timed out after Ns").
    statement_timeout_s: int | None = Field(default=60, gt=0)
