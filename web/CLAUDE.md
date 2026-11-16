# web/  — NOT STARTED (M5)

React/TypeScript GUI: import flow, library browser, review queue (DESIGN §6, §9).
Talks only to api/. Project root: ../CLAUDE.md

## What belongs here (authoritative)
- Frontend source, its package.json and build config.

If something does not clearly fit one of the above, it does not belong here.
"Not forbidden" is not the same as "allowed."

## Common misplacements
- Any SQL logic — the backend owns it; the GUI calls the API.

## If you need to add something not on the allowlist
STOP. Do not place the file here on the assumption it kind of fits. Surface the case to
the user, propose the addition, and update this CLAUDE.md (and the root CLAUDE.md if the
change is project-wide) before placing the new code. The architecture evolves through
conscious decisions, not silent drift.
