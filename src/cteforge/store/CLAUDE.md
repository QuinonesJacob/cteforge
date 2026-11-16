# src/cteforge/store/  — NOT STARTED (M3)

I/O adapter: database persistence (SQLite locally, Postgres on a server).
Project root: ../../../CLAUDE.md

## What belongs here (authoritative)
- SQLAlchemy models and repositories for definitions, versions, recipes, joins, proposals, users/roles.
- Migrations.

If something does not clearly fit one of the above, it does not belong here.
"Not forbidden" is not the same as "allowed."

## Common misplacements
- Definition serialization — reuse domain/codec.py.
- Approval rules — services/ (policy) and trust/ (labels).

## Interactions
Imports domain only. Imported only by services. Must not import other adapters.

## If you need to add something not on the allowlist
STOP. Do not place the file here on the assumption it kind of fits. Surface the case to
the user, propose the addition, and update this CLAUDE.md (and the root CLAUDE.md if the
change is project-wide) before placing the new code. The architecture evolves through
conscious decisions, not silent drift.
