# src/cteforge/trust/  — NOT STARTED (M2)

Pure policy: given how a result was built, return its TrustLabel (DESIGN §8).
Project root: ../../../CLAUDE.md

## What belongs here (authoritative)
- Label policy functions and the strict/open team setting logic.

If something does not clearly fit one of the above, it does not belong here.
"Not forbidden" is not the same as "allowed."

## Common misplacements
- Definition state transitions persisted to a database — store/ via services/.

## Interactions
Imports domain (and may read parsing/templating outputs). Imported by services.

## If you need to add something not on the allowlist
STOP. Do not place the file here on the assumption it kind of fits. Surface the case to
the user, propose the addition, and update this CLAUDE.md (and the root CLAUDE.md if the
change is project-wide) before placing the new code. The architecture evolves through
conscious decisions, not silent drift.
