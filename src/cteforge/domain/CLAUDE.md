# src/cteforge/domain/

Pure data models of cteforge. Everything else depends on this; it depends on nothing in
cteforge. Project root: ../../../CLAUDE.md

## What belongs here (authoritative)
- `models.py` — Definition, Annotation, VariableSpec, VariableSlot (one place a
  variable fills; M1 files migrate on read), DefinitionVersionInfo, Recipe (+
  `uses_definition`, `with_definition_renamed`), DefinitionRef, FormerId (a renamed
  definition's earlier id → its current id; ADR 0024), JoinEdge, JoinKey, DefinitionState,
  TrustLabel (+ TRUST_ORDER), TeamMode.
- `variables.py` — VariableType enum and the strict input model + validation per type.
- `dialects.py` — Dialect enum.
- `codec.py` — definition file format: dict/YAML text ⇄ Definition, with schema_version.
- `errors.py` — CteforgeError and subclasses (incl. EmbeddingError: the model can't load/run).
- `issues.py` — Issue, IssueCode, Severity: structured problems/warnings (ADR 0015).
  IssueCode is append-only; add new codes at the end of their group with Edit.
- `users.py` — User (`role` = a role NAME; `active`; the acting user's resolved `tools` /
  `review_all`, None = its built-in role's), UserRole (the built-in role names), ActorKind
  (ADR 0018, 0027). No tokens or hashes here.
- `access.py` — ADR 0027: the `Tool` catalog (one name per operation; MCP tools use the
  same names), `ToolGroup` + `TOOL_GROUPS` (use/build/review/admin), `ALL_TOOLS`,
  `ADMIN_ONLY_TOOLS`, `tool_group`, `sorted_tools` (catalog order), `ToolMode`
  (follow_role | only), `Role` (name Ident ≤ 32, description, allowed, defaults ⊆
  allowed, review_all, builtin), `TokenInfo` (token metadata: id, user_name, name, prefix,
  created/last-used/expiry/revoked times, tool_mode, include, exclude; `usable_at`; never
  the token or its hash), `BUILTIN_ROLES` (viewer, contributor, approver, admin) and
  `ADMIN_ROLE`. How roles and tokens combine is policy: trust/approval.py.
- `proposals.py` — Proposal, ProposalKind (incl. `rename_definition`, ADR 0024),
  ProposalStatus, `DEFINITION_KINDS`, `proposal_with_definition_renamed` (every stored copy
  of a definition id in a proposal → the new id; a rename's from/to kept) (DESIGN §18).
- `audit.py` — AuditEvent, AuditKind, AuditOutcome (ADR 0008).
- `search.py` — meaning search (ADR 0023): `search_document(definition, joins=(),
  former_ids=())` → the
  generated Markdown search document (pure, deterministic; no version/state/SQL);
  `document_sha256`; `SearchVector` (definition_id, version, model, doc_sha256, vector).
- `connections.py` — WarehouseProfile: connection options + env-var NAMES of secrets,
  row/byte caps and `statement_timeout_s` (default 60, None = off; ADR 0020). Never
  secret values.

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
