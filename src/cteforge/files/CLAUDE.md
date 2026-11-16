# src/cteforge/files/

I/O adapter: definition files on disk. Project root: ../../../CLAUDE.md

## What belongs here (authoritative)
- `definitions.py` — load/save Definition and annotation YAML files (via domain/codec.py),
  and read trusted-query `.sql` files.

If something does not clearly fit one of the above, it does not belong here.
"Not forbidden" is not the same as "allowed."

## Common misplacements
- Format/schema logic — domain/codec.py. This module only reads and writes bytes.

## Interactions
Imports domain only. Imported only by services. Must not import other adapters.

## If you need to add something not on the allowlist
STOP. Do not place the file here on the assumption it kind of fits. Surface the case to
the user, propose the addition, and update this CLAUDE.md (and the root CLAUDE.md if the
change is project-wide) before placing the new code. The architecture evolves through
conscious decisions, not silent drift.
