# src/cteforge/compose/  — NOT STARTED (M2)

Combines definitions into one query. Pure. Project root: ../../../CLAUDE.md

## What belongs here (authoritative)
- WITH-clause assembly from rendered definitions, dependency ordering.
- Grain and join-key compatibility checks between definitions.

If something does not clearly fit one of the above, it does not belong here.
"Not forbidden" is not the same as "allowed."

## Common misplacements
- Rendering a single definition — templating/.
- Choosing trust labels — trust/.

## Interactions
Imports domain, parsing, templating. Imported by services.

## If you need to add something not on the allowlist
STOP. Do not place the file here on the assumption it kind of fits. Surface the case to
the user, propose the addition, and update this CLAUDE.md (and the root CLAUDE.md if the
change is project-wide) before placing the new code. The architecture evolves through
conscious decisions, not silent drift.
