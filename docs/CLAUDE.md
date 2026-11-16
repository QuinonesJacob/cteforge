# docs/

Design doc and decision records. Project root: ../CLAUDE.md

## What belongs here (authoritative)
- `DESIGN.md` — the single design document (what cteforge is and why).
- `decisions/NNNN-slug.md` — one ADR per decision: Status, Context, Decision, Consequences.

If something does not clearly fit one of the above, it does not belong here.
"Not forbidden" is not the same as "allowed."

## Common misplacements
- API/CLI reference generated from code — not yet; propose a docs/reference/ first.
- Module-level how-to notes — those go in the module's CLAUDE.md.

## Conventions
- A change that alters a decision updates DESIGN.md and the ADR in the same commit.
- Superseded ADRs are kept; mark Status: superseded by NNNN.

## If you need to add something not on the allowlist
STOP. Do not place the file here on the assumption it kind of fits. Surface the case to
the user, propose the addition, and update this CLAUDE.md (and the root CLAUDE.md if the
change is project-wide) before placing the new code. The architecture evolves through
conscious decisions, not silent drift.
