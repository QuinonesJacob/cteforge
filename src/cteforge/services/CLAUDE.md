# src/cteforge/services/

Use-cases. Orchestrates the pure core and the I/O adapters. The ONLY layer that does I/O
(through adapters). Project root: ../../../CLAUDE.md

## What belongs here (authoritative)
Support modules (shared by use-cases; not use-cases themselves):
- `context.py` — `AppConfig` (from env: `CTEFORGE_DB_URL` default `sqlite:///cteforge.db`,
  `CTEFORGE_CONNECTIONS` path to connections.yml, `CTEFORGE_DEFAULT_PROFILE`,
  `CTEFORGE_AUTH` on|off, `CTEFORGE_USER` for stdio/local, `CTEFORGE_SEARCH_EMBEDDINGS`
  on|off, `CTEFORGE_EMBED_CACHE` default `~/.cache/cteforge/models`) and `AppContext`
  (store, audit log, warehouse registry, config, `embedder` — None = meaning search off —
  and `background_model`). `open_context(config, *, embedder=<default>) -> AppContext`
  (`default_embedder(config)`: a lazy FastEmbedEmbedder, nothing loaded); `close()`.
  Settings helpers: `team_mode` / `set_team_mode` (key `mode`); `default_connection` /
  `save_default_connection` (key `default_connection`, None deletes it) and
  `default_connection_issues` (a `default_connection_missing` warning when the stored name
  is no longer configured).
  `redact_db_url(url)` / `AppConfig.display_db_url`: the URL with its password (and
  secret-named query parameters) as `***` — the only form ever printed or logged.
- `connections.py` — warehouse registry (`summaries()` → ProfileSummary name, dialect,
  `default`): profile name → lazily opened `Warehouse`. The default profile is the
  **effective default connection** (ADR 0021): the admin's setting (`team_default`
  callable, read on every lookup) when configured → `CTEFORGE_DEFAULT_PROFILE` → the only
  profile → none; a stored name no longer configured is skipped (`missing_team_default`).
  Every `resolve_name(None)` / `get()` / `summary()` / `profile()` uses it
  (dispatch on dialect to warehouse/ connectors), each wrapped in `SerializedWarehouse`
  (one call at a time per warehouse: connectors share one driver connection, so concurrent
  execute+fetch would mix result sets; a caller waits at most `BUSY_TIMEOUT_SECONDS` for
  the lock, then gets WarehouseError "busy"; `close()` waits at most
  `CLOSE_TIMEOUT_SECONDS`); `open_warehouse(path)` kept for the M1 CLI. Per-statement
  timeouts are enforced by the connectors (profile `statement_timeout_s`, default 60 s;
  warehouse/), not here.
- `auth.py` — token → User (SHA-256 lookup), `require(user, action)` via trust.approval,
  local-mode user (`local_user`: the stored CTEFORGE_USER as is; created as a human admin
  only while the store has no users and `create=True` (CLI, auth-off HTTP), never for
  stdio MCP (`create=False`), else PermissionDeniedError), token generation
  (`secrets.token_urlsafe`), shown once.
- `cards.py` — response models shared by use-cases and adapters: VariableCard (name, type,
  role, input_format, description, allowed, unit, column, shape, original_value,
  comparisons — on DefinitionCard only), DefinitionCard (`search_document` on request),
  SearchHit (with use_when, dont_use_when, grain, matched_terms, matched_fields, `match`
  keyword|meaning|both|None, `note`), SearchDocument, SearchIndexReport, RunOutcome (with `used`:
  UsedDefinition per version — state, verified_by, verified_at; `values_used` per alias),
  TableCard / ColumnCard, ParseReport, ProposalOutcome, ProposalView,
  DefinitionDrift, TeamSettings, AuditPage, DefinitionReferences / RecipeReference /
  ProposalReference (ADR 0024; DefinitionCard.former_ids); `json_safe` (enums → values) /
  `json_rows`.
- `_audit.py` — records an AuditEvent for a use-case call (`record`, fail closed by
  default); the one outcome rule (`failure_outcome`) and `audit_failures` (records a raised
  CteforgeError, best effort, and re-raises). Every use-case wraps its permission check in it.
- `_library.py` — library lookups shared by read/run and write/review (no permission checks,
  no audit): `parse_dialect`; `resolve_dialect(ctx, dialect, profile)` (explicit → the
  connection's dialect: `profile`, else the default connection → PolicyError); version
  visibility (`usable_states`, `visible_states`,
  `newest_in_states` — search shows, and compile_and_run floats to, the same version);
  rebuilding a stored definition (`original_sql`, `annotations_of`, `annotation_file`);
  the join map (`canonical_edge`, `join_edge_id`, `mined_edges`, `refresh_join_map`: joins
  between base tables, also through CTEs/derived tables whose key columns pass through
  unchanged from one base table (parsing's JoinInfo `*_base_table`/`base_keys`); usage_count
  over all latest definitions; `same_table`, `matching_edge`); definition lookup by id
  (ADR 0024): `get_definition` / `definition_not_found` — the ONE place a missing id
  becomes NotFoundError, a former id naming the current one ("Definition 'a' was renamed
  to 'c' on <date>; use the new ID."), used by describe, run, recipe checks, review,
  propose, revalidate; `check_id_available` (exists / another definition's former id →
  PolicyError; `renaming=` may take back its own); `definition_references` →
  DefinitionReferences (latest recipe versions using it, pending proposals targeting or
  using it, former ids, `OUTSIDE_WARNING`).
- `_meaning.py` — meaning search (ADR 0023; no permission checks, no audit):
  `document_for(definition, former_ids=())` (domain search document + `_library.mined_edges`;
  `former_ids_of(ctx)` maps current id → former ids);
  `similarities(ctx, query, candidates) -> Meaning` (cosine over stored vectors; a match
  needs ≥ `MIN_SIMILARITY` 0.58 and within `MAX_GAP` 0.08 of the best; embeds missing
  candidate vectors first — at most `INCREMENTAL_LIMIT` per search when
  `ctx.background_model`; a model not ready → wait (CLI) or load in background and answer
  by keyword (servers); never raises, returns a `note` instead); `ensure_vectors`,
  `index_definition` (after apply; only with a loaded model; never raises), `indexable`,
  `warm_up`, `start_background` (daemon thread: load + backfill).
- `_apply.py` — proposal plumbing shared by propose, propose_other, review and library_io
  (no permission checks of its own): definition checks via import_query, recipe checks
  (render + compose dry-run), owner rule, `submit` (store + auto-apply via trust.approval),
  `apply_proposal` (new version / join edge; VERIFIED only on a human verify, never for a
  DRAFT proposal; refreshes the join map; then `_meaning.index_definition`), diff/impact `view`, `validation_detail`; renames (ADR 0024): `rename_proposal` (payload
  from_id, to_id, references), applied by `Store.rename_definition` (no new version, no
  diff; `_check_base` refuses a rename whose target was renamed meanwhile —
  `stale_rename_error` — or whose new id was taken; `verify_refusal` refuses verify). DRAFT
  definition proposals are never auto-applied. Join proposals are checked on the default
  profile (`join_checks`: tables and key columns exist) and carry `payload.base_join`
  (stale → StaleVersionError). `ensure_read_only` (called first by `checked_definition`)
  refuses definition SQL that is not one read-only SELECT/UNION (compose.checks.
  ensure_query) with PolicyError + a `not_read_only` issue, before any check runs.
  Decisions are serialized in-process (`DECISIONS`, the stored
  proposal is re-read and must be pending); separate processes are not covered.
- `import_query.py` — the checks engine for a trusted query + annotations (DESIGN §7:
  same query back, types, EXPLAIN, result comparison): `import_query`, `fetch_schema`,
  `analyze_query`; plus the M1 file-based `import_files` / `analyze_file` used by the CLI.
  Runtime (not EXPLAIN) failures of check runs keep only the error class: driver messages
  can quote data values and proposal issues are readable by any viewer. SQL in another
  dialect than the warehouse's is checked transpiled to it (`dialect_mismatch` warning;
  ADR 0013 addendum).
  Support module: parse_query, propose, revalidate and library_io build on it.
- `render_definition.py` — M1 file-based render/run (CLI). No ctx/user (pre-M3 path).
Use-cases (one file each; a use-case never imports another use-case — share via support
modules or the core):
- `search.py` — `search(ctx, user, query, *, limit=10, include_drafts=False, question=None) -> list[SearchHit]` (DESIGN §19, ADR 0017, 0023):
  BM25 keyword ranking (unchanged) + `_meaning.similarities`, merged by reciprocal rank
  fusion (`RRF_K` 60; equal scores share a rank). Without meaning matches the scores are
  plain BM25.
- `search_index.py` — `rebuild_search_index(ctx, user, *, force=False) ->
  SearchIndexReport` (admin; removes other models' vectors, or all with force, then embeds
  missing/changed); `download_model(config) -> Path` (no ctx/user: operator tooling for
  offline servers); `start_background(ctx)` (servers: `serve` lifespan, stdio `mcp`).
- `describe.py` — `get_definition(ctx, user, id, version=None, *, include_source=False,
  include_search_document=False) -> DefinitionCard` (with history; `include_source` adds
  `source_sql` and `annotations` for the GUI's revise flow; `include_search_document` the
  Markdown search document); `definition_history(ctx, user, id) ->
  list[DefinitionVersionInfo]`; `search_document(ctx, user, id, version=None) ->
  SearchDocument`; `definition_references(ctx, user, id) -> DefinitionReferences` (ADR
  0024). A former id raises NotFoundError naming the current id; the card has `former_ids`.
- `parse_query.py` — `parse_query(ctx, user, sql, dialect=None, profile=None) -> ParseReport`
  (dialect None → `_library.resolve_dialect`; with no profile named, a stale stored default
  connection adds a `default_connection_missing` warning).
- `propose.py` — `propose_definition(ctx, user, *, sql, dialect=None, metadata, annotations,
  profile=None, summary="")` (dialect None → `_library.resolve_dialect` with `profile` or
  metadata.profile), `revise_definition(ctx, user, *, id, base_version, sql=None,
  dialect=None, metadata=None, annotations=None, summary="")` → ProposalOutcome (DESIGN §18).
  Stale base → raises StaleVersionError with the `stale_base_version` Issue on `.issues`.
  `rename_definition(ctx, user, *, id, new_id, base_version=None, summary="")` →
  ProposalOutcome (ADR 0024; `revise_definition(new_id=...)` with nothing else delegates to
  it): valid Ident (CodecError), not the same id / not taken / not another definition's
  former id (PolicyError); same auto-apply rule as revisions. `propose_definition` refuses a
  former id.
- `propose_other.py` — `propose_join(ctx, user, *, left_table, right_table, keys,
  join_type="inner", relationship=None, traps=(), summary="")`,
  `propose_recipe(ctx, user, *, recipe, summary="")` → ProposalOutcome. `RecipeInput` is
  the caller's recipe (MCP/API schema); a mapping setting `version`, `state` or
  `schema_version` raises CodecError (cteforge sets them on apply).
- `review.py` — `list_proposals(ctx, user, status=None)`, `get_proposal(ctx, user, id)` →
  ProposalView (diff + impact); `approve(ctx, user, id, *, verify=False, comment="")` →
  ProposalOutcome; `reject(ctx, user, id, comment)`, `withdraw(ctx, user, id)` →
  ProposalView; `verify_definition(ctx, user, id, version)` → DefinitionCard;
  `count_proposals(ctx, user, status=None)` → int (nav badge: no diffs, not audited).
- `run.py` — `compile_and_run(ctx, user, *, uses=None, recipe_id=None, values=None,
  select=None, dialect=None, profile=None, run=True, limit=100, question=None)`,
  `check_and_run(ctx, user, sql, *, profile=None, run=True, limit=100, question=None)` →
  RunOutcome (render → compose → trust → explain → run → audit; SQL tagged per ADR 0008).
  A `dialect` other than the profile's is compile-only (`run=False`; no EXPLAIN,
  `checks_incomplete`); with `run=True` it is a PolicyError (DESIGN §12).
  check_and_run adds a `matches_definition` info issue when the SQL is AST-equal to a
  usable definition's original query (use compile_and_run with that id instead).
- `catalog.py` — discovery: `list_recipes(ctx, user, *, query="", include_drafts=False)`
  → list[Recipe]; `list_joins(ctx, user, *, table=None, include_drafts=False)` →
  list[JoinEdge]; `describe_tables(ctx, user, tables=None, *, profile=None)` →
  list[TableCard] (columns + types from the warehouse, definitions reading it, join edges,
  units the library's variables declare; at most 50 tables). Visibility as search.
- `revalidate.py` — `revalidate(ctx, user, ids=None) -> list[DefinitionDrift]`: drift check
  over latest versions (DESIGN §10, §18); reports, never changes state.
- `admin.py` — users, roles, tokens, settings (team mode); an agent admin may not create
  a human user, change a human's role or rotate another human's token (ADR 0007):
  `bootstrap_admin(ctx, name)`
  (only while no users exist), `create_user(ctx, user, name, role, kind)` → (User, token
  shown once), `list_users`, `set_role` (last admin can't be demoted), `rotate_token` →
  token (admin or self), `get_settings` / `set_settings(mode)` /
  `set_default_connection(ctx, user, name | None)` (unknown profile → NotFoundError; blank
  clears) → TeamSettings (mode, default_connection as stored, effective_default_connection,
  issues).
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
| get_definition, definition_history, search_document, definition_references | DESCRIBE (viewer) | get_definition (op) | best effort |
| parse_query | DESCRIBE (viewer) | parse_query (SQL as SHA-256 only) | best effort |
| compile_and_run | RUN (viewer) | compile_and_run | closed: written before rows return |
| check_and_run | RUN (viewer) | check_and_run | closed: written before rows return |
| revalidate(ids) / revalidate() | RUN (viewer) / approver role (whole library) | revalidate | closed |
| propose_definition, revise_definition, rename_definition, propose_join, propose_recipe | PROPOSE (contributor); owner rule (`_apply.check_owner`) | propose (op; a rename also lists `old@v` and `new@v` and `extra.renamed_from/renamed_to`) | closed |
| import_library | PROPOSE (contributor) | propose (op) | closed |
| list_proposals, get_proposal | DESCRIBE (viewer) | review (op) | best effort |
| list_recipes, list_joins, describe_tables | DESCRIBE (viewer) | get_definition (op) | best effort |
| count_proposals | SEARCH (viewer) | none (cheap badge poll) | — |
| approve, reject | APPROVE on target (approver/admin, or the definition's owner; a VERIFIED target needs a human); approve(verify=True) also VERIFY | review (op) | closed |
| withdraw | PROPOSE, and the proposal's author | review (op) | closed |
| verify_definition | VERIFY (human approver/admin; owners may not); four-eyes over every author since the last logic change | review (op) | closed |
| list_audit | approver role (APPROVE, no target); then bad `kind` / naive `since` → PolicyError | admin (op=list_audit) | best effort |
| export_library | DESCRIBE (viewer) | admin (op) | closed |
| bootstrap_admin | none; only while no users exist | admin (op) | closed |
| create_user, list_users, set_role | MANAGE_USERS (admin); agents not over human users | admin (op) | closed (list: best effort) |
| rotate_token | the user themselves, else MANAGE_USERS (agents not for a human) | admin (op) | closed |
| get_settings, set_settings, set_default_connection | MANAGE_SETTINGS (admin) | admin (op) | closed (get: best effort) |
| rebuild_search_index | MANAGE_SETTINGS (admin); meaning search off → PolicyError | admin (op=rebuild_search_index) | closed |

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
