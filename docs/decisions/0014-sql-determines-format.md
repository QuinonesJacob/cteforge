# 0014 — The SQL determines the format; variables are plain values

Status: accepted · 2026-11-16

## Context
Values in trusted SQL appear in many forms: `CAST('2026-01-01' AS TIMESTAMP)`,
`strptime('01-01-2026', '%m-%d-%Y')`, a bare `'01-15-2026'`, `500 * 1e12` (unit scaling),
`'%' || 'H100' || '%'` (a LIKE pattern). "Format" is really three different problems:
spelling (how a date is written as text), units (what a number means), and shape around the
value (wildcards, functions, arithmetic).

## Decision
- Agents always send **plain values in human terms**: ISO dates, plain numbers, plain text.
- Whatever the analyst wrapped around a value in the SQL (casts, functions, arithmetic,
  concatenation) **stays exactly as written**. The tool replaces only the literal itself.
  So conversions live in the analyst's SQL, under their control.
- Parsing must detect literals inside value-side wrappers (functions, arithmetic,
  concatenation) and report the wrapper, so they can become variables.
- `format` (optional, `date`/`date_range` only): a strftime pattern for a bare date string
  whose text is not ISO. Rendering writes the date in that pattern; the original value is
  read with it. When the format is passed as a literal argument next to the value (e.g.
  `strptime(v, '%m-%d-%Y')`, `TO_DATE(v, 'MM-DD-YYYY')`), parsing suggests it.
- Number spelling has no `format`. Units are meaning, not spelling: an optional `unit` label
  (`number` only, e.g. `TFLOPS`) is shown to agents so they convert correctly.
- The "same query back" check renders original values through `format`; a wrong pattern
  that cannot parse the original fails import. It cannot tell day-month from month-day when
  both are ≤ 12, so the tool warns (`ambiguous_date_format`) unless the format came from a
  format-string argument in the SQL.

## Consequences
- No per-type formatting language; the SQL stays the place where conversion is expressed.
- Column-side expressions (e.g. `CAST(REPLACE(compute, ' TFLOPS', '') AS DOUBLE) >= 500`)
  must be typed as the expression's type, not the raw column's (see ADR 0015).
