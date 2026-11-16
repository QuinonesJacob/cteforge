# src/cteforge/audit/  — NOT STARTED (M3)

I/O adapter: cteforge's own audit log, and the SQL comment tag (ADR 0008).
Project root: ../../../CLAUDE.md

## What belongs here (authoritative)
- Event writer (calls, definitions+versions, values, label, result metadata — never rows).
- SQL tag builder: /* cteforge: <def> v<n> compile_id=... */.

If something does not clearly fit one of the above, it does not belong here.
"Not forbidden" is not the same as "allowed."

## Common misplacements
- Warehouse query history — read from the warehouse via warehouse/, never copied here.

## Interactions
Imports domain only. Imported only by services.

## If you need to add something not on the allowlist
STOP. Do not place the file here on the assumption it kind of fits. Surface the case to
the user, propose the addition, and update this CLAUDE.md (and the root CLAUDE.md if the
change is project-wide) before placing the new code. The architecture evolves through
conscious decisions, not silent drift.
