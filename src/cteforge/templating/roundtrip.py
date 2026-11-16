""" "Same query back" check (DESIGN §7).

Render the stored template text with the original values and compare the parsed tree to
the parsed original query. Because the template is re-parsed from text, this catches
annotations that swapped, dropped or mis-typed literals. Services additionally compare
query results on the warehouse — that is the stronger proof.
"""

from __future__ import annotations

import difflib

from pydantic import BaseModel, ConfigDict

from cteforge.domain.models import Definition
from cteforge.parsing.analyze import parse
from cteforge.templating.render import render_tree


class RoundTripReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    identical: bool
    diff: str = ""  # Unified diff of original vs rendered SQL when not identical.


def roundtrip_check(original_sql: str, definition: Definition) -> RoundTripReport:
    dialect = str(definition.source_dialect)
    original = parse(original_sql, definition.source_dialect)
    rendered, _, _ = render_tree(definition, {})
    if rendered == original:
        return RoundTripReport(identical=True)
    diff = "\n".join(
        difflib.unified_diff(
            original.sql(dialect=dialect, pretty=True).splitlines(),
            rendered.sql(dialect=dialect, pretty=True).splitlines(),
            fromfile="original",
            tofile="rendered_with_original_values",
            lineterm="",
        )
    )
    return RoundTripReport(identical=False, diff=diff)
