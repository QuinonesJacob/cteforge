"""Read warehouse connection profiles from ``connections.yml`` (ADR 0020).

Format::

    profiles:
      demo:
        dialect: duckdb
        options: {path: warehouse.duckdb}      # relative DuckDB paths: next to this file
      prod:
        dialect: postgres
        options: {host: db.internal, dbname: analytics, user: cteforge_ro}
        secrets_env: {password: PG_PASSWORD}   # env-var NAMES, never secret values
        max_rows: 5000

Secrets never live in this file: secret-looking option names (password, token,
private_key, credentials, ...) and URLs with embedded credentials are refused, and error
messages never echo option values.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import CodecError

# Option names that hold a secret or a connection string that may embed one. Path options
# such as BigQuery's `credentials_file` are allowed (they point at a file, not a secret).
_SECRET_KEY = re.compile(
    r"pass(word|wd|code|phrase)|pwd|token|secret|private_?key|api_?key|access_?key"
    r"|^credentials?(_json)?$|conninfo|dsn",
    re.IGNORECASE,
)
# scheme://user:password@host
_URL_WITH_PASSWORD = re.compile(r"[a-z][a-z0-9+.-]*://[^/\s@]*:[^/\s@]*@", re.IGNORECASE)
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PROFILE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
_PROFILE_KEYS = {"dialect", "options", "secrets_env", "max_rows", "maximum_bytes_billed"}
_USE_SECRETS_ENV = (
    "put secrets in environment variables and reference them by name in secrets_env, "
    "e.g. secrets_env: {password: PG_PASSWORD}"
)


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that refuses duplicate mapping keys (a silently shadowed profile)."""


def _construct_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode) -> dict[Any, Any]:
    seen: set[Any] = set()
    for key_node, _value in node.value:
        key = loader.construct_object(key_node)
        if key in seen:
            raise CodecError(f"Duplicate key '{key}' (line {key_node.start_mark.line + 1})")
        seen.add(key)
    return dict(loader.construct_mapping(node))


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,  # type: ignore[arg-type]
)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CodecError(f"Cannot read connections file {path}: {exc.strerror}") from exc


def _check_no_inline_secrets(name: str, body: dict[str, Any]) -> None:
    options = body.get("options") or {}
    if not isinstance(options, dict):
        raise CodecError(f"Profile '{name}': options must be a mapping")
    secret_keys = sorted(str(k) for k in options if _SECRET_KEY.search(str(k)))
    if secret_keys:
        raise CodecError(
            f"Profile '{name}' sets secret-looking options {secret_keys} inline; "
            + _USE_SECRETS_ENV
        )
    with_password = sorted(
        str(k) for k, v in options.items() if isinstance(v, str) and _URL_WITH_PASSWORD.search(v)
    )
    if with_password:
        raise CodecError(
            f"Profile '{name}' option(s) {with_password} hold a URL with embedded "
            "credentials; " + _USE_SECRETS_ENV
        )
    secrets_env = body.get("secrets_env") or {}
    if not isinstance(secrets_env, dict):
        raise CodecError(f"Profile '{name}': secrets_env must be a mapping")
    bad = sorted(
        str(k)
        for k, v in secrets_env.items()
        if not (isinstance(v, str) and _ENV_NAME.fullmatch(v))
    )
    if bad:
        raise CodecError(
            f"Profile '{name}': secrets_env {bad} must name environment variables "
            "(letters, digits, underscores), not hold the secret itself"
        )


def _validation_message(exc: ValidationError) -> str:
    """Field locations and reasons only; never the input values (they may be secrets)."""
    parts = []
    for err in exc.errors(include_input=False, include_url=False):
        loc = ".".join(str(p) for p in err["loc"]) or "profile"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)


def _resolve_duckdb_path(body: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    options = dict(body.get("options") or {})
    path = options.get("path")
    if isinstance(path, str) and path and path != ":memory:":
        p = Path(path).expanduser()
        options["path"] = str(p if p.is_absolute() else (base_dir / p).resolve())
    return {**body, "options": options}


def _profile(name: Any, body: Any, base_dir: Path) -> WarehouseProfile:
    if not isinstance(name, str) or not _PROFILE_NAME.fullmatch(name):
        raise CodecError(f"Invalid profile name {name!r}: use letters, digits, '_', '-' or '.'")
    if not isinstance(body, dict):
        raise CodecError(f"Profile '{name}' must be a mapping with at least 'dialect'")
    _check_no_inline_secrets(name, body)
    unknown = sorted(str(k) for k in body if k not in _PROFILE_KEYS)
    if unknown:
        raise CodecError(
            f"Profile '{name}' has unknown keys {unknown}; allowed: {sorted(_PROFILE_KEYS)}"
        )
    if body.get("dialect") == Dialect.DUCKDB.value:
        body = _resolve_duckdb_path(body, base_dir)
    try:
        return WarehouseProfile.model_validate({**body, "name": name})
    except ValidationError as exc:
        raise CodecError(f"Profile '{name}' is invalid: {_validation_message(exc)}") from None


def profiles_from_yaml(text: str, base_dir: str | Path = ".") -> list[WarehouseProfile]:
    """Parse connections YAML. Relative DuckDB paths resolve against `base_dir`."""
    try:
        data = yaml.load(text, Loader=_UniqueKeyLoader)  # noqa: S506 - SafeLoader subclass
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" (line {mark.line + 1})" if mark is not None else ""
        raise CodecError(f"Connections file is not valid YAML{where}") from None
    if data is None:
        return []
    if not isinstance(data, dict) or set(data) - {"profiles"}:
        raise CodecError("Connections file must be a mapping with a single 'profiles' key")
    profiles = data.get("profiles") or {}
    if not isinstance(profiles, dict):
        raise CodecError("'profiles' must map profile names to settings")
    base = Path(base_dir)
    return [_profile(name, body, base) for name, body in profiles.items()]


def load_profiles(path: str | Path) -> list[WarehouseProfile]:
    """Load profiles from a connections.yml file (file order)."""
    p = Path(path)
    return profiles_from_yaml(_read(p), base_dir=p.parent.resolve())
