# src/cteforge/cli/

Typer CLI. Thin adapter over services. Project root: ../../../CLAUDE.md

## What belongs here (authoritative)
- `main.py` — commands: parse, render (with --check, --run). Argument parsing and output formatting only.

If something does not clearly fit one of the above, it does not belong here.
"Not forbidden" is not the same as "allowed."

## Common misplacements
- Any logic beyond calling a service and printing the result — services/.

## Interactions
Imports services (and domain for types/errors only).

## If you need to add something not on the allowlist
STOP. Do not place the file here on the assumption it kind of fits. Surface the case to
the user, propose the addition, and update this CLAUDE.md (and the root CLAUDE.md if the
change is project-wide) before placing the new code. The architecture evolves through
conscious decisions, not silent drift.
