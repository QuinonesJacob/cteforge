# src/cteforge/services/

Use-cases. Orchestrates the pure core and the I/O adapters. The ONLY layer that does I/O
(through adapters). Project root: ../../../CLAUDE.md

## What belongs here (authoritative)
Support modules (shared by use-cases; not use-cases themselves):
- `context.py` — `AppConfig` (from env: `CTEFORGE_DB_URL` default `sqlite:///cteforge.db`,
  `CTEFORGE_CONNECTIONS` path to connections.yml, `CTEFORGE_DEFAULT_PROFILE`,
  `CTEFORGE_AUTH` on|off, `CTEFORGE_USER` for stdio/local) and `AppContext` (store, audit
  log, warehouse registry, config). `open_context(config) -> AppContext`; `close()`.
- `connections.py` — warehouse registry: profile name → lazily opened `Warehouse`
  (dispatch on dialect to warehouse/ connectors), each wrapped in `SerializedWarehouse`
  (one call at a time per warehouse: connectors share one driver connection, so concurrent
  execute+fetch would mix result sets); `open_warehouse(path)` kept for the M1 CLI.
- `auth.py` — token → User (SHA-256 lookup), `require(user, action)` via trust.approval,
  local-mode user (`local_user`: the stored CTEFORGE_USER as is; created as a human admin
  only while the store has no users, else PermissionDeniedError), token generation
  (`secrets.token_urlsafe`), shown once.
- `cards.py` — response models shared by use-cases and adapters: VariableCard (name, type,
  role, input_format, description, allowed, unit, column, shape, original_value),
  DefinitionCard, SearchHit, RunOutcome (with `used`: UsedDefinition per version — state,
  verified_by, verified_at), ParseReport, ProposalOutcome, ProposalView,
  DefinitionDrift, TeamSettings, AuditPage; `json_safe` (enums → values) / `json_rows`.
- `_audit.py` — records an AuditEvent for a use-case call (`record`, fail closed by
  default); the one outcome rule (`failure_outcome`) and `audit_failures` (records a raised
  CteforgeError, best effort, and re-raises). Every use-case wraps its permission check in it.
- `_library.py` — library lookups shared by read/run and write/review (no permission checks,
  no audit): `parse_dialect`; version visibility (`usable_states`, `visible_states`,
  `newest_in_states` — search shows, and compile_and_run floats to, the same version);
  rebuilding a stored definition (`original_sql`, `annotations_of`, `annotation_file`);
  the join map (`canonical_edge`, `join_edge_id`, `mined_edges`, `refresh_join_map`: base-
  table joins only, usage_count over all latest definitions; `same_table`, `matching_edge`).
- `_apply.py` — proposal plumbing shared by propose, propose_other, review and library_io
  (no permission checks of its own): definition checks via import_query, recipe checks
  (render + compose dry-run), owner rule, `submit` (store + auto-apply via trust.approval),
  `apply_proposal` (new version / join edge; VERIFIED only on a human verify, never for a
  DRAFT proposal; refreshes the join map), diff/impact `view`, `validation_detail`. DRAFT
  definition proposals are never auto-applied. Join proposals are checked on the default
  profile (`join_checks`: tables and key columns exist) and carry `payload.base_join`
  (stale → StaleVersionError). Decisions are serialized in-process (`DECISIONS`, the stored
  proposal is re-read and must be pending); separate processes are not covered.
- `import_query.py` — the checks engine for a trusted query + annotations (DESIGN §7:
  same query back, types, EXPLAIN, result comparison): `import_query`, `fetch_schema`,
  `analyze_query`; plus the M1 file-based `import_files` / `analyze_file` used by the CLI.
  Support module: parse_query, propose, revalidate and library_io build on it.
- `render_definition.py` — M1 file-based render/run (CLI). No ctx/user (pre-M3 path).
Use-cases (one file each; a use-case never imports another use-case — share via support
modules or the core):
- `search.py` — `search(ctx, user, query, *, limit=10, include_drafts=False, question=None) -> list[SearchHit]` (DESIGN §19, ADR 0017).
- `describe.py` — `get_definition(ctx, user, id, version=None) -> DefinitionCard` (with
  history); `definition_history(ctx, user, id) -> list[DefinitionVersionInfo]`.
- `parse_query.py` — `parse_query(ctx, user, sql, dialect, profile=None) -> ParseReport`.
- `propose.py` — `propose_definition(ctx, user, *, sql, dialect, metadata, annotations,
  profile=None, summary="")`, `revise_definition(ctx, user, *, id, base_version, sql=None,
  dialect=None, metadata=None, annotations=None, summary="")` → ProposalOutcome (DESIGN §18).
  Stale base → raises StaleVersionError with the `stale_base_version` Issue on `.issues`.
- `propose_other.py` — `propose_join(ctx, user, *, left_table, right_table, keys,
  join_type="inner", relationship=None, traps=(), summary="")`,
  `propose_recipe(ctx, user, *, recipe, summary="")` → ProposalOutcome.
- `review.py` — `list_proposals(ctx, user, status=None)`, `get_proposal(ctx, user, id)` →
  ProposalView (diff + impact); `approve(ctx, user, id, *, verify=False, comment="")` →
  ProposalOutcome; `reject(ctx, user, id, comment)`, `withdraw(ctx, user, id)` →
  ProposalView; `verify_definition(ctx, user, id, version)` → DefinitionCard.
- `run.py` — `compile_and_run(ctx, user, *, uses=None, recipe_id=None, values=None,
  select=None, dialect=None, profile=None, run=True, limit=100, question=None)`,
  `check_and_run(ctx, user, sql, *, profile=None, run=True, limit=100, question=None)` →
  RunOutcome (render → compose → trust → explain → run → audit; SQL tagged per ADR 0008).
- `revalidate.py` — `revalidate(ctx, user, ids=None) -> list[DefinitionDrift]`: drift check
  over latest versions (DESIGN §10, §18); reports, never changes state.
- `admin.py` — users, roles, tokens, settings (team mode); an agent admin may not create
  a human user, change a human's role or rotate another human's token (ADR 0007):
  `bootstrap_admin(ctx, name)`
  (only while no users exist), `create_user(ctx, user, name, role, kind)` → (User, token
  shown once), `list_users`, `set_role` (last admin can't be demoted), `rotate_token` →
  token (admin or self), `get_settings` / `set_settings(mode)` → TeamSettings.
- `audit_log.py` — `list_audit(ctx, user, *, limit=100, actor=None, kind=None,
  definition_id=None, since=None) -> AuditPage` (limit 1..1000, exact `has_more`).
- `library_io.py` — export/import the library as YAML files (git optional, ADR 0003) via
  files/library.py: `export_library(ctx, user, directory, *, prune=False)` → written paths;
  `import_library(ctx, user, directory, *, profile=None)` → list[ProposalOutcome] (every
  changed file becomes a proposal; unchanged files are skipped; every definition proposal
  is built and checked before any is stored; each submitted proposal is audited at once).

## Permissions and audit per use-case (contract)
Roles: viewer < contributor < approver < admin (ADR 0018); agents never verify (ADR 0007).
"Audit" is the AuditKind recorded (with `extra.op` where one kind covers several calls) and
whether a failed write fails the call (closed) or is only logged (best effort).

| Use-case | Permission (`auth.require` / trust.approval) | AuditKind (`extra.op`) | Audit write |
|---|---|---|---|
| search | SEARCH (viewer) | search | best effort |
| get_definition, definition_history | DESCRIBE (viewer) | get_definition (op) | best effort |
| parse_query | DESCRIBE (viewer) | parse_query (SQL as SHA-256 only) | best effort |
| compile_and_run | RUN (viewer) | compile_and_run | closed: written before rows return |
| check_and_run | RUN (viewer) | check_and_run | closed: written before rows return |
| revalidate(ids) / revalidate() | RUN (viewer) / approver role (whole library) | revalidate | closed |
| propose_definition, revise_definition, propose_join, propose_recipe | PROPOSE (contributor); owner rule (`_apply.check_owner`) | propose (op) | closed |
| import_library | PROPOSE (contributor) | propose (op) | closed |
| list_proposals, get_proposal | DESCRIBE (viewer) | review (op) | best effort |
| approve, reject | APPROVE on target (approver/admin, or the definition's owner; a VERIFIED target needs a human); approve(verify=True) also VERIFY | review (op) | closed |
| withdraw | PROPOSE, and the proposal's author | review (op) | closed |
| verify_definition | VERIFY (human approver/admin; owners may not) | review (op) | closed |
| list_audit | approver role (APPROVE, no target); then bad `kind` / naive `since` → PolicyError | admin (op=list_audit) | best effort |
| export_library | DESCRIBE (viewer) | admin (op) | closed |
| bootstrap_admin | none; only while no users exist | admin (op) | closed |
| create_user, list_users, set_role | MANAGE_USERS (admin); agents not over human users | admin (op) | closed (list: best effort) |
| rotate_token | the user themselves, else MANAGE_USERS (agents not for a human) | admin (op) | closed |
| get_settings, set_settings | MANAGE_SETTINGS (admin) | admin (op) | closed (get: best effort) |

Outcome rule (`_audit.failure_outcome`, applied by `_audit.audit_failures`): OK on success;
REJECTED for PermissionDeniedError, PolicyError, StaleVersionError and for refusals returned
as outcomes (trust/strict mode, not read-only; check_and_run also counts SQL that does not
parse as refused); ERROR for any other failure (class name only). Never stored: SQL text
(SHA-256 only), result rows, tokens, credentials.

Every use-case: takes `ctx` and the acting `user` first; checks permissions
(`auth.require`) inside `_audit.audit_failures`; records an audit event on success; raises
CteforgeError subclasses (never bare ValueError) or returns Issues.

If something does not clearly fit one of the above, it does not belong here.
"Not forbidden" is not the same as "allowed."

## Common misplacements
- SQL manipulation — parsing/, templating/, compose/. Label/approval policy — trust/.
- Argument parsing, printing, HTTP/MCP schemas — cli/, mcp/, api/ (they reuse cards.py models).

## Interactions
Imports everything in the core and adapters. Imported only by cli, mcp, api.

## If you need to add something not on the allowlist
STOP. Do not place the file here on the assumption it kind of fits. Surface the case to
the user, propose the addition, and update this CLAUDE.md (and the root CLAUDE.md if the
change is project-wide) before placing the new code. The architecture evolves through
conscious decisions, not silent drift.
