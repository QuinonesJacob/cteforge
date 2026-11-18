"""Search documents and stored search vectors (DESIGN §19, ADR 0023).

A **search document** is one Markdown text per definition version, generated (never
edited) from its fields. It is what the server embeds for meaning search. Pure: the same
definition (and joins) always gives the same text, so its SHA-256 says whether a stored
vector is still current.

Not in the document, on purpose: the version number, state and verification (verifying
must not change the text), owner and profile (not meaning), the SQL itself.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from cteforge.domain.models import Definition, JoinEdge


def _section(heading: str, body: str) -> str:
    return f"## {heading}\n\n{body.strip()}"


def _join_line(edge: JoinEdge) -> str:
    keys = " and ".join(
        f"{edge.left_table}.{k.left} = {edge.right_table}.{k.right}" for k in edge.keys
    )
    return f"- {edge.left_table} {edge.join_type} join {edge.right_table} on {keys}"


def search_document(
    definition: Definition, joins: Sequence[JoinEdge] = (), former_ids: Sequence[str] = ()
) -> str:
    """The Markdown search document of `definition`: title, id, former ids (ADR 0024: an
    agent searching an old name finds the definition), meaning, use / don't-use notes,
    keywords, variables (name, type, description, unit, allowed values), outputs, grain,
    tables and joins (`joins`: the joins its SQL makes, e.g. mined from it; plus its
    declared join keys). Empty fields are left out. Order is stable: the definition's own
    order for keywords, variables and outputs (the analyst's order); sorted for tables,
    joins and former ids (derived sets)."""
    d = definition
    parts = [f"# {d.title.strip()}", f"Definition `{d.id}`."]
    formers = sorted({f.strip() for f in former_ids if f.strip()} - {d.id})
    if formers:
        parts[-1] += " Formerly " + ", ".join(f"`{f}`" for f in formers) + "."
    if d.meaning.strip():
        parts.append(d.meaning.strip())
    if d.use_when.strip():
        parts.append(_section("Use when", d.use_when))
    if d.dont_use_when.strip():
        parts.append(_section("Don't use when", d.dont_use_when))
    keywords = [k.strip() for k in d.keywords if k.strip()]
    if keywords:
        parts.append(_section("Keywords", ", ".join(keywords)))
    if d.variables:
        lines = []
        for v in d.variables:
            line = f"- `{v.name}` ({v.type.value}, {v.role.value})"
            details = [v.description.strip()] if v.description.strip() else []
            if v.unit:
                details.append(f"unit: {v.unit}")
            if v.allowed:
                details.append("allowed: " + ", ".join(str(a) for a in v.allowed))
            if details:
                line += ": " + "; ".join(details)
            lines.append(line)
        parts.append(_section("Variables", "\n".join(lines)))
    if d.composition is not None:
        # ADR 0026: a composed definition is found by the parts it is built from.
        lines = [
            f"- `{alias}`: `{ref.id}`" + (f" v{ref.version}" if ref.version is not None else "")
            for alias, ref in sorted(d.composition.uses.items())
        ]
        parts.append(_section("Built from", "\n".join(lines)))
    if d.outputs:
        parts.append(_section("Outputs", ", ".join(d.outputs)))
    if d.grain and d.grain.strip():
        parts.append(_section("Grain", d.grain))
    if d.tables:
        parts.append(_section("Tables", "\n".join(f"- {t}" for t in sorted(set(d.tables)))))
    join_lines = sorted({_join_line(e) for e in joins})
    if d.join_keys:
        join_lines.append("- Join on output columns: " + ", ".join(d.join_keys))
    if join_lines:
        parts.append(_section("Joins", "\n".join(join_lines)))
    return "\n\n".join(parts) + "\n"


def document_sha256(text: str) -> str:
    """Hex SHA-256 of a search document (UTF-8): a stored vector is current when the
    document it was made from has the same hash."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class SearchVector(BaseModel):
    """A definition version's search document as embedded by one model. Derived data:
    it can always be rebuilt from the definition."""

    model_config = ConfigDict(frozen=True)

    definition_id: str
    version: int
    model: str  # Embedding model name, e.g. "BAAI/bge-small-en-v1.5".
    doc_sha256: str  # document_sha256 of the text it was made from.
    vector: tuple[float, ...] = Field(min_length=1)

    @property
    def dim(self) -> int:
        return len(self.vector)
