# src/cteforge/templating/

Turns a trusted query + annotations into a template, and a template + values into SQL.
Pure. Project root: ../../../CLAUDE.md

## What belongs here (authoritative)
- `template.py` — apply_annotations(sql, dialect, analysis, annotations) → AnnotatedTemplate
  (template, variables, issues): replace annotated literals with named placeholders written
  in the source dialect's syntax (e.g. `$x` in DuckDB, ADR 0011) and report Issues.
- `literals.py` — the ONLY place values become SQL: typed literal nodes + casts per slot.
- `render.py` — render(definition, values, dialect) → RenderResult(sql, dialect, values,
  issues, transpiled). Never emits placeholders. `render_tree` is shared with roundtrip.
- `roundtrip.py` — same-query-back check: serialize → re-parse → render originals → AST compare.

If something does not clearly fit one of the above, it does not belong here.
"Not forbidden" is not the same as "allowed."

## How it works (keep in sync with the code)
- **Places and slots** (ADR 0015). An annotation's `literals` are grouped into places:
  date_range = `[start, end]` pairs in order; text_list = whole IN lists, a new place
  starting where `LiteralInfo.in_list` changes; other types = one literal each. Every place
  must hold the same original value (AnnotationError otherwise). Each literal position is a
  `VariableSlot` (placeholder, field, literal IDs, literal_form, cast_type, format,
  original_text); `VariableSpec.slots` lists them place by place, so render and the round
  trip are deterministic. Placeholders: place 1 `period__start` / `regions`, place n ≥ 2
  `period__2__start` / `regions__2` (`domain.variables.placeholder_name`).
- **Stored layout.** Definition files keep `schema_version: 1`; the M1 layout (literal_form /
  cast_type on the variable, one place) is still read — `domain/codec.py` converts it into
  one place of slots — and files are always written in the slot layout. The examples are
  regenerated with `cteforge import` (tests check they match a fresh import).
- **AnnotationError vs Issue.** AnnotationError only when no template can be built: unknown or
  reused literal IDs, wrong arity, a value that doesn't fit the type (e.g. 'open' as a date),
  places with different values, `format` on a non-date / `unit` on a non-number, a format that
  can't read (or write back exactly) the original text, a text_list whose IN list holds
  wrapped or non-literal items (the whole list is replaced, so wrappers would be lost), a
  range pair listed upper-bound first. Everything else is an Issue so the analyst sees all of
  it at once: `type_mismatch` (error), `type_unknown` (warning), `range_not_simple` (warning),
  `description_required` (error), `ambiguous_date_format` (warning).
- **Types** are checked per literal against `LiteralInfo.compared_type` (the column-side
  expression's type), falling back to the column's type. No type → `type_unknown`.
- **date_range shape**: the parsing range candidate's shape when every place is a candidate
  pair with the same shape; otherwise `null` + `range_not_simple`.
- **description_required**: a changeable variable with an empty description whose literals
  are `negated` / `or_branch` / `case_condition` / `outer_join_on`, or a date_range with no shape.
- **Dates and format** (ADR 0014). Bare date text is read and written with the slot's format:
  `Annotation.format` if given, else the literal's `suggested_format`, else ISO. Typed
  literals (`DATE '...'`, CAST) are always ISO. `VariableSpec.format` records the analyst's
  explicit format only. `ambiguous_date_format` when an explicit format differs from what the
  SQL suggests and the original's day and month are both ≤ 12 and differ.
- **Numbers.** Values are Decimal inside (domain validates: JSON numbers only, finite). A slot
  keeps the literal's `original_text`; when the value equals it numerically, that exact text
  is rendered (`1000.50`, `1e3`), else the plain decimal (`format(d, "f")`). Numbers render
  as numeric literal nodes (negative: Neg), never identifiers. Original literals with more
  precision than a JSON number carries are rejected. `unit` (number only) is carried as is.
- **Values only replace the literal**: wrappers (strptime, `? * 1e12`, `'%' || ? || '%'`)
  stay as written; the slot sits where the literal was.
- **Casts** (DESIGN §13). A typed slot is cast to its `cast_type` built in the source dialect
  (falling back to the variable's `column_type`), so naive/zone-aware semantics carry over.
  When transpiling, sqlglot's zone-naive TIMESTAMP/DATETIME is spelled TIMESTAMPNTZ, so
  Snowflake gets TIMESTAMP_NTZ, BigQuery DATETIME and Databricks TIMESTAMP_NTZ (generic
  TIMESTAMP means LTZ in Databricks). Only variable slots are adjusted; the analyst's other
  casts transpile as sqlglot does.
- **Render issues**: `fixed_override` (a fixed variable given a different value),
  `value_not_allowed` (outside `allowed`), `transpiled` (target ≠ source). All warnings.

## Known transpile defects (sqlglot 30.31.0; visible in tests/golden, not fixed here)
- Postgres: `ROUND(x / y, 4)` becomes `ROUND(CAST(x AS DOUBLE PRECISION) / y, 4)`; Postgres
  has no ROUND(double precision, integer), so the query fails.
- BigQuery: DuckDB `DATE_TRUNC('MONTH', ts)` on a naive timestamp becomes
  `TIMESTAMP_TRUNC(ts, MONTH)`; the column is DATETIME in BigQuery (needs DATETIME_TRUNC).
- BigQuery / Postgres: DuckDB `strptime(...)` (naive) becomes `PARSE_TIMESTAMP` /
  `TO_TIMESTAMP`, which return zone-aware values compared with naive columns.
Transpiled renders always carry a `transpiled` issue and lower trust (ADR 0013).

## Common misplacements
- Input validation of values — domain/variables.py (call it, don't duplicate it).
- Running SQL to compare results — services/ (uses warehouse/).
- Combining several definitions — compose/ (M2).

## Interactions
Imports domain, parsing. Imported by compose, services.

## If you need to add something not on the allowlist
STOP. Do not place the file here on the assumption it kind of fits. Surface the case to
the user, propose the addition, and update this CLAUDE.md (and the root CLAUDE.md if the
change is project-wide) before placing the new code. The architecture evolves through
conscious decisions, not silent drift.
