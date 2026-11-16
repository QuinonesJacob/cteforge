# src/cteforge/domain/

Pure data models of cteforge. Everything else depends on this; it depends on nothing in
cteforge. Project root: ../../../CLAUDE.md

## What belongs here (authoritative)
- `models.py` — Definition, VariableSpec, Recipe, JoinEdge, DefinitionState, TrustLabel.
- `variables.py` — VariableType enum and the strict input model + validation per type.
- `dialects.py` — Dialect enum.
- `codec.py` — definition file format: dict/YAML text ⇄ Definition, with schema_version.
- `errors.py` — CteforgeError and subclasses.

If something does not clearly fit one of the above, it does not belong here.
"Not forbidden" is not the same as "allowed."

## Common misplacements
- sqlglot imports or SQL rendering — templating/.
- Reading/writing files — files/. Codec takes and returns text/dicts only.
- Warehouse type mapping — parsing/types.py.

## Interactions
Imported by every other module. Imports only pydantic, pyyaml and the standard library.

## Conventions
- No I/O, no env reads, no sqlglot. Models are pydantic v2.

## If you need to add something not on the allowlist
STOP. Do not place the file here on the assumption it kind of fits. Surface the case to
the user, propose the addition, and update this CLAUDE.md (and the root CLAUDE.md if the
change is project-wide) before placing the new code. The architecture evolves through
conscious decisions, not silent drift.
