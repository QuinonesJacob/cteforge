# tests/

pytest suite. Project root: ../CLAUDE.md

## What belongs here (authoritative)
- `unit/<module>/test_*.py` — mirrors src/cteforge/<module>/; pure, no warehouse.
- `integration/` — tests using a DuckDB warehouse (built in a tmp dir by fixtures).
- `golden/` — per-dialect expected SQL snapshots (ADR 0012).
- `conftest.py` — shared fixtures.

If something does not clearly fit one of the above, it does not belong here.
"Not forbidden" is not the same as "allowed."

## Common misplacements
- Sample data meant for demos — examples/.

## Conventions
- Assert on returned values, never on source text. Every assertion must be able to fail.
- Golden files update only deliberately (UPDATE_GOLDEN=1) and are reviewed as diffs.

## If you need to add something not on the allowlist
STOP. Do not place the file here on the assumption it kind of fits. Surface the case to
the user, propose the addition, and update this CLAUDE.md (and the root CLAUDE.md if the
change is project-wide) before placing the new code. The architecture evolves through
conscious decisions, not silent drift.
