"""Compile ids and the SQL comment tag that joins warehouse logs to ours (ADR 0008).

Strict by design: any input outside the allowed alphabet raises ``ValueError`` instead of
being silently stripped, so a tag can never carry ``*/``, a newline or other SQL.
"""

from __future__ import annotations

import re
import secrets

_DEFINITION = re.compile(r"[a-z0-9_]+@[0-9]+")
_COMPILE_ID = re.compile(r"[0-9a-f]{1,64}")
_COMPILE_ID_BYTES = 6  # 12 hex chars
_TAG_OPEN = "/* cteforge: "
_TAG_CLOSE = " */"


def new_compile_id() -> str:
    """Return a fresh 12-character lowercase hex compile id."""
    return secrets.token_hex(_COMPILE_ID_BYTES)


def _definition_part(entry: str) -> str:
    if not _DEFINITION.fullmatch(entry):
        raise ValueError(f"definition must be '<id>@<version>' with id in [a-z0-9_]: {entry!r}")
    def_id, version = entry.split("@")
    return f"{def_id} v{int(version)}"


def sql_tag(definitions: list[str], compile_id: str) -> str:
    """Build ``/* cteforge: <id> v<n>[, ...] compile_id=<hex> */``.

    Empty ``definitions`` gives the exploratory form. Raises ``ValueError`` on any entry that
    is not ``<id>@<digits>`` with id in ``[a-z0-9_]``, or a compile id that is not lowercase hex.
    """
    if not isinstance(compile_id, str) or not _COMPILE_ID.fullmatch(compile_id):
        raise ValueError(f"compile_id must be 1-64 lowercase hex chars: {compile_id!r}")
    if definitions:
        body = ", ".join(_definition_part(entry) for entry in definitions)
    else:
        body = "exploratory"
    return f"{_TAG_OPEN}{body} compile_id={compile_id}{_TAG_CLOSE}"


def tag_sql(sql: str, tag: str) -> str:
    """Prefix ``sql`` with ``tag`` on its own line.

    Raises ``ValueError`` unless ``tag`` is one single-line ``/* cteforge: ... */`` comment.
    """
    if not _is_single_tag(tag):
        raise ValueError(f"not a single-line cteforge tag: {tag!r}")
    return f"{tag}\n{sql}"


def _is_single_tag(tag: str) -> bool:
    if len(tag) <= len(_TAG_OPEN) + len(_TAG_CLOSE):
        return False
    if not (tag.startswith(_TAG_OPEN) and tag.endswith(_TAG_CLOSE)):
        return False
    inner = tag[len(_TAG_OPEN) : -len(_TAG_CLOSE)]
    return not any(bad in inner for bad in ("*/", "/*", "\n", "\r"))
