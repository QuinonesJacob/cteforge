# 0013 — Dialect policy

Status: accepted · 2026-11-15

## Decision
A definition records its source dialect and renders natively in it. Cross-dialect rendering is allowed but labeled and lowers trust.

## Consequences
- Golden tests cover native rendering for each dialect and the transpile path separately.

## Addendum (2026-11-16)
- `compile_and_run` with a `dialect` other than the profile's is allowed with `run=false`
  (compiled only, no EXPLAIN, `checks_incomplete`); with `run=true` it is refused, since the
  SQL can only run in the warehouse's own dialect.
- Proposals whose SQL dialect differs from the checking profile's are checked on the SQL
  transpiled to the profile's dialect (both the original and the rendered template), with a
  `dialect_mismatch` warning instead of a blocking error. Column types come from that
  warehouse. Runs on that profile are transpiled and labeled as above.
