# src/cteforge/files/

I/O adapter: definition files on disk. Project root: ../../../CLAUDE.md

## What belongs here (authoritative)
- `definitions.py` — load/save Definition and annotation YAML files (via domain/codec.py),
  and read trusted-query `.sql` files.
- `connections.py` — `load_profiles(path) -> list[WarehouseProfile]` from `connections.yml`
  (`profiles: {name: {dialect, options, secrets_env, max_rows, maximum_bytes_billed,
  statement_timeout_s}}`,
  ADR 0020); refuses inline secrets (secret-looking option names, URLs with passwords,
  secrets_env values that aren't env-var names) with CodecError, never echoing values.
  Relative DuckDB `options.path` resolves next to the file.
- `library.py` — library directory for git export/import (ADR 0003):
  `write_library(dir, *, definitions, recipes, joins, prune=False) -> list[Path]` and
  `read_library(dir) -> LibraryFiles` (`definitions/<id>.yml` codec YAML,
  `recipes/<id>.yml`, `joins/<id>.yml` as model fields). CodecError names the file only.

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
