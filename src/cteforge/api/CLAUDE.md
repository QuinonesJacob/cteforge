# src/cteforge/api/  — NOT STARTED (M5)

HTTP API for the GUI. Thin adapter over services. Project root: ../../../CLAUDE.md

## What belongs here (authoritative)
- Routes, request/response schemas, auth middleware.

If something does not clearly fit one of the above, it does not belong here.
"Not forbidden" is not the same as "allowed."

## Common misplacements
- Business logic — services/.

## Interactions
Imports services (and domain for types/errors only).

## If you need to add something not on the allowlist
STOP. Do not place the file here on the assumption it kind of fits. Surface the case to
the user, propose the addition, and update this CLAUDE.md (and the root CLAUDE.md if the
change is project-wide) before placing the new code. The architecture evolves through
conscious decisions, not silent drift.
