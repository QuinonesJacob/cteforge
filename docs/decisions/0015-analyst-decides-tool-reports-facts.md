# 0015 — The analyst decides; the tool reports structure, suggests, never assumes meaning

Status: accepted · 2026-11-16

## Context
M1 auto-paired two date bounds on the same column into a "range" and recorded its shape from
the comparison operators alone, ignoring `NOT`, `OR` and `CASE`. The rendered SQL was always
correct (it keeps whatever logic the analyst wrote), but the label shown to agents could be
wrong — e.g. `NOT BETWEEN` labeled "rows in this period". Queries with several windows on one
column could not be annotated at all.

## Decision
- Every literal in a filter position gets an ID. Each is either left alone (part of the
  SQL), a **fixed** variable (named and described, visible to agents, overriding warns), or a
  **changeable** variable. The analyst (person or LLM) decides; nothing is changeable by default.
- Parsing reports **structural facts** per literal, not meaning: `negated` (under NOT, or compared with an excluding operator such as `<>`),
  `or_branch` (inside an OR), `case_condition`, `join_on`, `outer_join_on` (ON of an outer
  join — filters attached rows, not the driving rows), `having`, `subquery`, and any
  value-side wrapper.
- Range **suggestions** pair a lower and upper bound only when they are direct operands of
  the same AND chain (or one BETWEEN), on the same column expression. Only then is a shape
  recorded: `half_open` [a, b), `inclusive` [a, b], `exclusive` (a, b), `start_exclusive`
  (a, b]. The chain itself may sit under `NOT` or inside one `OR` branch (e.g. an excluded
  window, or one of two windows); that is reported as context on both literals, never hidden.
  Bounds in different AND chains (`a OR b`, CASE vs WHERE) are never paired.
- A changeable variable whose literals carry `negated`, `or_branch`, `case_condition` or
  `outer_join_on` context must have a description (`description_required`), because its
  name alone can't convey what it does.
- An analyst may still annotate any two bounds as a `date_range`. If they are not a
  suggested pair, the shape is `null`, the import returns a `range_not_simple` issue, and the
  variable must have a description before the definition can leave `draft`.
- One variable may bind the same filter in several places (e.g. the same period in two CTEs):
  `literals` may list a multiple of the type's arity, and every group must hold the same
  original value.
- Problems and warnings are structured `Issue`s (code, severity, message, variable, literal),
  not free text, so agents and the GUI can act on them.
- Column types are looked up case-insensitively for unquoted identifiers, with schema
  qualifiers kept. When the column side is an expression, its type (via sqlglot type
  annotation) is what the variable must match.

## Consequences
- Labels shown to agents are never stronger than what the SQL structurally guarantees.
- Complex filters stay supported; they just need a human-written description.
