"""Application configuration and context shared by every use-case (ADR 0016, 0018, 0020).

`AppConfig.from_env()` is the single place that reads `CTEFORGE_*` environment variables.
`open_context(config)` opens the store and the audit log on the same database URL, creates
their schemas, and loads warehouse profiles. Use-cases receive the resulting `AppContext`.
"""

from __future__ import annotations

import datetime as dt
import os
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import TracebackType

from pydantic import BaseModel, ConfigDict, field_validator

from cteforge.audit.log import AuditLog
from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.errors import PolicyError
from cteforge.domain.models import TeamMode
from cteforge.files.connections import load_profiles
from cteforge.services.connections import WarehouseRegistry
from cteforge.store.store import Store

DEFAULT_DB_URL = "sqlite:///cteforge.db"
DEFAULT_USER = "local"
MODE_SETTING = "mode"  # Store setting key holding the TeamMode.

Clock = Callable[[], dt.datetime]

_REDACTED = "***"
# Query parameters whose values are secrets (e.g. ?password=... in a SQLAlchemy URL).
_SECRET_PARAM = re.compile(
    r"([?&](?:password|passwd|pwd|secret|token|access_token|api_key|sslpassword)=)[^&#]*",
    re.IGNORECASE,
)


def redact_db_url(url: str) -> str:
    """A database URL safe to print or log: the password (and secret-named query
    parameters) replaced by ***, e.g. postgresql+psycopg://user:***@host/db.
    The user, host and database are kept; URLs without credentials are unchanged."""
    scheme, sep, rest = url.partition("://")
    if sep:
        stops = [i for i in (rest.find(c) for c in "/?#") if i != -1]
        at = rest.rfind("@", 0, min(stops, default=len(rest)))
        if at == -1:
            at = rest.find("@")  # a password with an unencoded '/', '?' or '#'
        if at != -1:
            user, has_password, _ = rest[:at].partition(":")
            if has_password:
                rest = f"{user}:{_REDACTED}{rest[at:]}"
        url = f"{scheme}{sep}{rest}"
    return _SECRET_PARAM.sub(lambda m: m.group(1) + _REDACTED, url)


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class AuthMode(StrEnum):
    ON = "on"  # HTTP calls need a bearer token (ADR 0018).
    OFF = "off"  # Every HTTP call acts as the local admin; localhost only.


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    db_url: str = DEFAULT_DB_URL
    connections_path: Path | None = None
    default_profile: str | None = None
    auth: AuthMode = AuthMode.ON
    user: str = DEFAULT_USER  # Acting user for stdio MCP, the CLI, and auth=off.

    @field_validator("user")
    @classmethod
    def _user_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("user must not be blank")
        return value.strip()

    @property
    def display_db_url(self) -> str:
        """`db_url` with its password redacted, for printing and logs."""
        return redact_db_url(self.db_url)

    @property
    def auth_enabled(self) -> bool:
        return self.auth is AuthMode.ON

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> AppConfig:
        """Read CTEFORGE_DB_URL, CTEFORGE_CONNECTIONS, CTEFORGE_DEFAULT_PROFILE,
        CTEFORGE_AUTH (on|off, default on) and CTEFORGE_USER (default "local").
        Empty values count as unset. Raises PolicyError on an invalid CTEFORGE_AUTH."""
        source = os.environ if env is None else env

        def get(name: str) -> str | None:
            value = source.get(name, "").strip()
            return value or None

        auth_raw = (get("CTEFORGE_AUTH") or AuthMode.ON.value).lower()
        try:
            auth = AuthMode(auth_raw)
        except ValueError:
            raise PolicyError("CTEFORGE_AUTH must be 'on' or 'off'") from None
        connections = get("CTEFORGE_CONNECTIONS")
        return cls(
            db_url=get("CTEFORGE_DB_URL") or DEFAULT_DB_URL,
            connections_path=Path(connections).expanduser() if connections else None,
            default_profile=get("CTEFORGE_DEFAULT_PROFILE"),
            auth=auth,
            user=get("CTEFORGE_USER") or DEFAULT_USER,
        )


@dataclass
class AppContext:
    """Everything a use-case needs. Close it (or use it as a context manager) when done."""

    config: AppConfig
    store: Store
    audit: AuditLog
    warehouses: WarehouseRegistry
    clock: Clock = field(default=utc_now)
    _closed: bool = field(default=False, init=False, repr=False)

    def now(self) -> dt.datetime:
        """Current time (timezone-aware UTC). Injectable via `clock` for tests."""
        return self.clock()

    def close(self) -> None:
        """Close warehouses, then the store and audit engines. Safe to call twice."""
        if self._closed:
            return
        self._closed = True
        try:
            self.warehouses.close_all()
        finally:
            try:
                self.store.close()
            finally:
                self.audit.close()

    def __enter__(self) -> AppContext:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def open_context(
    config: AppConfig | None = None,
    *,
    profiles: Iterable[WarehouseProfile] | None = None,
    clock: Clock = utc_now,
) -> AppContext:
    """Open store + audit log on `config.db_url` (schemas created) and the warehouse
    registry. `profiles` overrides loading `config.connections_path` (tests, embedding).
    No profiles configured → an empty registry (callers get PolicyError on use)."""
    config = config or AppConfig.from_env()
    if profiles is None:
        profiles = load_profiles(config.connections_path) if config.connections_path else []
    warehouses = WarehouseRegistry(profiles, default=config.default_profile)
    store = Store(config.db_url)
    audit: AuditLog | None = None
    try:
        store.init_schema()
        audit = AuditLog(config.db_url)
        audit.init_schema()
    except BaseException:
        store.close()
        if audit is not None:
            audit.close()
        raise
    return AppContext(config=config, store=store, audit=audit, warehouses=warehouses, clock=clock)


# ── Settings helpers ─────────────────────────────────────────────────────────────────


def team_mode(ctx: AppContext) -> TeamMode:
    """The team's mode (DESIGN §8): store setting "mode", default open. An unreadable
    stored value fails safe to strict."""
    raw = ctx.store.get_setting(MODE_SETTING)
    if raw is None:
        return TeamMode.OPEN
    try:
        return TeamMode(raw)
    except ValueError:
        return TeamMode.STRICT


def set_team_mode(ctx: AppContext, mode: TeamMode) -> TeamMode:
    """Persist the team mode. No permission check: callers (admin.py) `require` first."""
    mode = TeamMode(mode)
    ctx.store.set_setting(MODE_SETTING, mode.value)
    return mode
