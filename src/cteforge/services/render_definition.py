"""Use-case: render a definition with new values, optionally check and run it.

The core of `compile_and_run` for a single definition (DESIGN §6). Agents send values;
SQL comes from the template, never from the agent (ADR 0005).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import RenderError
from cteforge.domain.models import Definition
from cteforge.files.definitions import load_definition
from cteforge.templating.render import RenderResult, render
from cteforge.warehouse.base import QueryResult, Warehouse


class RenderOutcome(BaseModel):
    """Rendered SQL (issues on `render.issues`) and, with run=True, the result rows."""

    model_config = ConfigDict(frozen=True)

    render: RenderResult
    result: QueryResult | None = None


def render_definition(
    definition: Definition,
    values: dict[str, Any],
    dialect: Dialect | None = None,
    warehouse: Warehouse | None = None,
    warehouse_dialect: Dialect | None = None,
    run: bool = False,
    limit: int = 100,
) -> RenderOutcome:
    rendered = render(definition, values, dialect)
    if not run:
        return RenderOutcome(render=rendered)
    if warehouse is None:
        raise RenderError("--run needs a warehouse")
    wh_dialect = warehouse_dialect or warehouse.dialect
    if rendered.dialect is not wh_dialect:
        raise RenderError(
            f"Rendered for {rendered.dialect} but the warehouse is {wh_dialect}; "
            f"pass --dialect {wh_dialect} (lowers trust) or use a {rendered.dialect} warehouse"
        )
    warehouse.explain(rendered.sql)
    return RenderOutcome(render=rendered, result=warehouse.run(rendered.sql, limit))


def render_file(definition_path: str, values: dict[str, Any], **kwargs: Any) -> RenderOutcome:
    """File-based wrapper used by the CLI."""
    return render_definition(load_definition(definition_path), values, **kwargs)
