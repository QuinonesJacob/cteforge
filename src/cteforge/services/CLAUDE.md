# src/cteforge/services/

Use-cases. Orchestrates the pure core and the I/O adapters. The ONLY layer that does I/O
(through adapters). Project root: ../../../CLAUDE.md

## What belongs here (authoritative)
Support modules (shared by use-cases; not use-cases themselves):
- `context.py` — `AppConfig` (from env: `CTEFORGE_DB_URL` default `sqlite:///cteforge.db`,
  `CTEFORGE_CONNECTIONS` path to connections.yml, `CTEFORGE_DEFAULT_PROFILE`,
  `CTEFORGE_AUTH` on|off, `CTEFORGE_USER` for stdio/local, `CTEFORGE_TOKEN` for stdio MCP
  (a SecretStr, never printed; ADR 0027), `CTEFORGE_SEARCH_EMBEDDINGS`
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
- `auth.py` — ADR 0018, 0027. `new_token` (`cfg_pat_` + 43 URL-safe chars), `hash_token`
  (SHA-256, the only stored form), `display_prefix` (12 chars). `authenticate(ctx, token)`
  → the acting User with its effective tools (`Store.user_by_token_hash` +
  `trust.approval.effective_tools`; unknown / revoked / expired / deactivated → the same
  generic PermissionDeniedError; last_used_at written at most once a minute, best effort).
  `local_user(ctx, *, create=True, defaults_only=False)` (the stored CTEFORGE_USER with its
  role's allowed tools, or defaults; deactivated → refused; created as a human admin only
  while the store has no users and `create=True` (CLI, auth-off HTTP), never for stdio MCP),
  `stdio_user` (CTEFORGE_TOKEN's user and tools, else CTEFORGE_USER with role defaults),
  `resolve_user` (HTTP). An authenticated User also carries `token_id`,
  `token_expires_at`, `token_mode` of the calling token (None without one). `require(user, tool, target=None)` / `permitted`: tool membership
  (REVIEW_PROPOSAL / VERIFY consult the target via can_approve / can_verify).
  `user_tools(user)` (what MCP shows), `refuse_tool(ctx, user, name)` (audits an MCP call
  to a hidden tool: admin, op=tool_refused, rejected; returns the PermissionDeniedError).
- `cards.py` — response models shared by use-cases and adapters: VariableCard (name, type,
  role, input_format, description, allowed, unit, column, shape, original_value,
  comparisons — on DefinitionCard only), DefinitionCard (`search_document` on request),
  SearchHit (with use_when, dont_use_when, grain, matched_terms, matched_fields, `match`
  keyword|meaning|both|None, `note`), SearchDocument, SearchIndexReport, RunOutcome (with `used`:
  UsedDefinition per version — state, verified_by, verified_at; `values_used` per alias),
  TableCard / ColumnCard, ParseReport, ProposalOutcome, ProposalView,
  DefinitionDrift, TeamSettings, AuditPage, DefinitionReferences / UsedByReference /
  ProposalReference (ADR 0024, 0026; DefinitionCard.former_ids); ADR 0026: PartCard,
  CompositionCard (DefinitionCard.composition / ProposalView.composition), JoinCard (edge +
  known, confirmed, pending_proposals), DefinitionCard.composed / used_by / joins,
  LibraryPage / LibraryRow / ProposalBrief; access cards (ADR 0027):
  ToolInfo + `tool_catalog()`, RoleCard (+ `role_card`, `users` count), TokenCard (+
  `token_card`: effective tools now, state active|revoked|expired; never the value or
  hash), NewToken {token (shown once), info}, UserView (user + tokens), MeCard (tools,
  review_all, role_allowed, role_defaults); DefinitionCard.verified_by_kind;
  `json_safe` (enums → values) / `json_rows`.
- `_audit.py` — records an AuditEvent for a use-case call (`record`, fail closed by
  default); the one outcome rule (`failure_outcome`) and `audit_failures` (records a raised
  CteforgeError, best effort, and re-raises). Every use-case wraps its permission check in it.
- `_library.py` — library lookups shared by read/run and write/review (no permission checks,
  no audit): `parse_dialect`; `resolve_dialect(ctx, dialect, profile)` (explicit → the
  connection's dialect: `profile`, else the default connection → PolicyError); version
  visibility (`usable_states`, `visible_states`,
  `newest_in_states` — search shows, and compile_and_run floats to, the same version);
  rebuilding a stored definition (`original_sql` — a composed definition's final SELECT,
  "" when none —, `annotations_of`, `annotation_file`);
  the join map (`canonical_edge`, `join_edge_id`, `flip_relationship`, `mined_edges(definition,
  sql=None)` — composed ones only from their compiled SQL —, `refresh_join_map(ctx,
  sql_of)`: mines every latest definition, inserts new edges, recomputes `sources` and
  usage_count, keeps annotations; joins between base tables, also through CTEs/derived
  tables whose key columns pass through unchanged from one base table (parsing's JoinInfo
  `*_base_table`/`base_keys`); `trusted_sources` via `person_stands_behind` /
  `human_backing` (verified, or authored / approved by a human, not auto-applied);
  `known_edges` (ADR 0026: mined from a definition a person stands behind, added or
  confirmed by a person); `same_table`, `matching_edge`, `final_select_joins` (raw-table joins of a final
  SELECT vs known edges); `join_cards(ctx, user, edges)` → JoinCard); definition lookup by id
  (ADR 0024): `get_definition` / `definition_not_found` — the ONE place a missing id
  becomes NotFoundError, a former id naming the current one ("Definition 'a' was renamed
  to 'c' on <date>; use the new ID."), used by describe, run, compositions, review,
  propose, revalidate; `check_id_available` (exists / another definition's former id →
  PolicyError; `renaming=` may take back its own); `definition_references` →
  DefinitionReferences (`used_by`: latest composed definitions using it, pending proposals
  targeting or using it, former ids, `OUTSIDE_WARNING`); `used_by(ctx, id)`.
- `_compose.py` — composed definitions (ADR 0026; no permission checks, no audit):
  `resolve` (pinned / floating part; `part_not_usable` error, `part_superseded` info),
  `split_values` (`<alias>.<var>` or per alias), `compile_definition(ctx, definition,
  values, target, *, mode, prefix)` → Compiled (SQL; issues; dotted values; the SQL and
  composed definitions used, for trust; joins_known / raw_tables of unverified final
  SELECTs; blocking) — recursive, cycles → PolicyError `composition_cycle`, depth ≤ 10;
  `compiled_sql` (never raises), `cycle_issue`, `open_variables`, `composition_card`,
  `checked_composition(ctx, user, …)` (proposal checks: read-only select, template + same
  query back, parts resolve, fixed names open variables, no cycle, compiles + EXPLAIN,
  results compared when the select is annotated; state llm_annotated / draft; a final
  SELECT reading raw tables needs `user` to hold check_and_run before it runs on the
  warehouse); `require_exploratory(user, raw)` (the ADR 0027 raw-table gate).
- `_upgrade.py` — run by `open_context` once per open: audits the store's schema-3
  migration note (admin event, op `migrate_adr_0026`, actor `cteforge`, fail closed, then
  cleared) and re-mines the join map once per mining-rule version (setting
  `join_map_version`, 3: trusted_sources). `review.verify_definition` re-mines too (a
  verified definition's joins become known).
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
  (no permission checks of its own): definition checks via import_query, composition
  checks (`checked_composition` → `_compose`), owner rule, `submit` (store + auto-apply via trust.approval),
  `apply_proposal` (new version / join edge; VERIFIED only on a human verify, never for a
  DRAFT proposal; a person's approval of a join sets `confirmed_by`; refreshes the join
  map; then `_meaning.index_definition`), diff/impact (used by) + composition `view`,
  `validation_detail`; renames (ADR 0024): `rename_proposal` (payload
  from_id, to_id, references), applied by `Store.rename_definition` (no new version, no
  diff; `_check_base` refuses a rename whose target was renamed meanwhile —
  `stale_rename_error` — or whose new id was taken; `verify_refusal` refuses verify). DRAFT
  definition proposals are never auto-applied. Join proposals (`join_proposal(..., action)`)
  are checked on the default profile (`join_checks`: tables and key columns exist;
  `relationship_evidence`: COUNT(*) vs COUNT(DISTINCT key) per side through
  compose.relationship, `relationship_contradicted` error, evidence in the payload and on
  the edge — only for a new or changed claim, a proposer holding check_and_run, and tables
  and key columns join_checks positively confirmed; else a `checks_incomplete` warning
  and no counts) and carry `payload.base_join` (stale → StaleVersionError); an agent's
  `add` never auto-applies; joins are never verified; approving a change to a confirmed
  edge's relationship / traps / notes keeps it confirmed only for a human approver. A composed definition proposal is
  re-checked for cycles when applied. `ensure_read_only` (called first by `checked_definition`)
  refuses definition SQL that is not one read-only SELECT/UNION (compose.checks.
  ensure_query) with PolicyError + a `not_read_only` issue, before any check runs.
  Decisions are serialized in-process (`DECISIONS`, the stored
  proposal is re-read and must be pending); separate processes are not covered.
- `import_query.py` — the checks engine for a trusted query + annotations (DESIGN §7:
  same query back, types, EXPLAIN, result comparison): `import_query`, `fetch_schema`,
  `analyze_query`, `compare_queries` (also used for composed definitions); plus the M1 file-based `import_files` / `analyze_file` used by the CLI.
  Runtime (not EXPLAIN) failures of check runs keep only the error class: driver messages
  can quote data values and proposal issues are readable by any viewer. SQL in another
  dialect than the warehouse's is checked transpiled to it (`dialect_mismatch` warning;
  ADR 0013 addendum).
  Support module: parse_query, propose, revalidate and library_io build on it.
- `render_definition.py` — M1 file-based render/run (CLI). No ctx/user (pre-M3 path).
Use-cases (one file each; a use-case never imports another use-case — share via support
modules or the core):
- `library.py` — `list_library(ctx, user, *, q="", trust=None, approval=None,
  created_by=None, verified_by=None, needs_my_review=False) -> LibraryPage` (ADR 0026: the
  GUI's Library — definitions + the proposals touching them; see its docstring for the
  rows and filters).
- `search.py` — `search(ctx, user, query, *, limit=10, include_drafts=False, question=None) -> list[SearchHit]` (DESIGN §19, ADR 0017, 0023; a composed definition's hit lists its open variables):
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
  0024). A former id raises NotFoundError naming the current id; the card has `former_ids`,
  `used_by`, `joins` (edges touching its tables) and, for a composed definition,
  `composition` and its open variables (ADR 0026).
- `parse_query.py` — `parse_query(ctx, user, sql, dialect=None, profile=None) -> ParseReport`
  (dialect None → `_library.resolve_dialect`; with no profile named, a stale stored default
  connection adds a `default_connection_missing` warning).
- `propose.py` — `propose_definition(ctx, user, *, sql=None, composition=None, dialect=None,
  metadata, annotations=(), profile=None, summary="")` (exactly one of sql / composition —
  `CompositionInput` {uses, select?, fixed?}, ADR 0026 — else PolicyError; dialect None →
  `_library.resolve_dialect` with `profile` or metadata.profile),
  `revise_definition(ctx, user, *, id, base_version, sql=None, composition=None,
  dialect=None, metadata=None, annotations=None, summary="")` → ProposalOutcome (DESIGN §18).
  Stale base → raises StaleVersionError with the `stale_base_version` Issue on `.issues`.
  `rename_definition(ctx, user, *, id, new_id, base_version=None, summary="")` →
  ProposalOutcome (ADR 0024; `revise_definition(new_id=...)` with nothing else delegates to
  it): valid Ident (CodecError), not the same id / not taken / not another definition's
  former id (PolicyError); same auto-apply rule as revisions. `propose_definition` refuses a
  former id.
- `propose_other.py` — `propose_join(ctx, user, *, action="annotate", id=None,
  left_table=None, right_table=None, keys=None, join_type=None, relationship=None,
  traps=None, notes=None, summary="")` → ProposalOutcome (ADR 0026: annotate an existing
  edge by id or tables + keys — NotFoundError otherwise; add an edge not in the map —
  PolicyError otherwise).
- `review.py` — `list_proposals(ctx, user, status=None)`, `get_proposal(ctx, user, id)` →
  ProposalView (diff + impact); `approve(ctx, user, id, *, verify=False, comment="")` →
  ProposalOutcome; `reject(ctx, user, id, comment)`, `withdraw(ctx, user, id)` →
  ProposalView; `verify_definition(ctx, user, id, version)` → DefinitionCard;
  `count_proposals(ctx, user, status=None)` → int (nav badge: no diffs, not audited).
- `run.py` — `compile_and_run(ctx, user, *, uses, values=None, select=None, dialect=None,
  profile=None, run=True, limit=100, question=None)` (composed definitions by id, through
  `_compose`),
  `check_and_run(ctx, user, sql, *, profile=None, run=True, limit=100, question=None)` →
  RunOutcome (render → compose → trust → explain → run → audit; SQL tagged per ADR 0008).
  A `dialect` other than the profile's is compile-only (`run=False`; no EXPLAIN,
  `checks_incomplete`); with `run=True` it is a PolicyError (DESIGN §12).
  check_and_run adds a `matches_definition` info issue when the SQL is AST-equal to a
  usable definition's original query (use compile_and_run with that id instead).
- `catalog.py` — discovery: `list_joins(ctx, user, *, table=None, known_only=False,
  definition=None)` → list[JoinCard]; `describe_tables(ctx, user, tables=None, *, profile=None)` →
  list[TableCard] (columns + types from the warehouse, definitions reading it, join edges,
  units the library's variables declare; at most 50 tables). Visibility as search.
- `revalidate.py` — `revalidate(ctx, user, ids=None) -> list[DefinitionDrift]`: drift check
  over latest versions (DESIGN §10, §18; composed definitions through
  `_compose.checked_composition`); reports, never changes state.
- `admin.py` — users, roles, tokens, settings (ADR 0018, 0027; who-may rules from
  trust.approval): `bootstrap_admin(ctx, name, kind)` → (User, "default" token shown once;
  only while no users exist), `create_user(ctx, user, name, role, kind, *,
  issue_token=True)` → (User, token | None: a "default" token only for a human caller with
  issue_token), `list_users` → UserView (tokens' metadata), `update_user(ctx, user, name,
  *, role=None, active=None)` / `set_role` / `set_active` → UserView (last active admin
  can't be demoted or deactivated), `me` → MeCard; roles: `list_roles`, `create_role`,
  `update_role` (admin role: Allowed and review_all fixed; shrinking allowed trims
  defaults), `delete_role` (custom, unused); tokens: `list_tokens(ctx, user, name=None)`,
  `create_token(ctx, user, owner=None, *, name, expires_at, tool_mode, include, exclude)`
  → NewToken (never for an agent caller; within the owner's role; self-service through a
  token never beyond that token's tools, expiry or `only` mode, and linked to it as
  `parent_token_id`), `revoke_token(ctx, user, token_id, owner=None)` (cascades to
  descendants),
  `set_token_tools(...)` → TokenCard; `get_settings` / `set_settings(mode)` /
  `set_default_connection(ctx, user, name | None)` (unknown profile → NotFoundError; blank
  clears) → TeamSettings (mode, default_connection as stored, effective_default_connection,
  issues).
- `audit_log.py` — `list_audit(ctx, user, *, limit=100, actor=None, kind=None,
  definition_id=None, since=None) -> AuditPage` (limit 1..1000, exact `has_more`).
- `library_io.py` — export/import the library as YAML files (git optional, ADR 0003) via
  files/library.py (composed definitions included; legacy recipe files imported as
  composed definitions): `export_library(ctx, user, directory, *, prune=False)` → written paths;
  `import_library(ctx, user, directory, *, profile=None)` → list[ProposalOutcome] (every
  changed file becomes a proposal; unchanged files are skipped; every definition proposal
  is built and checked before any is stored; each submitted proposal is audited at once;
  a new definition's id must pass `_library.check_id_available`, so a former id is refused).

## Permissions and audit per use-case (contract)
Permissions are tools (ADR 0027): every use-case requires one tool from the catalog
(domain/access.py) via `auth.require(user, Tool.X, target?)`; the acting user's tools are
its role ∩ its token's list (trust.approval). Built-in roles: viewer (use tools +
list_proposals) < contributor (+ build tools, review_proposal for what they own) <
approver (+ review_all, verify, view_audit) < admin (every tool). Agents verify only with
the admin role. "Audit" is the AuditKind recorded (with `extra.op` where one kind covers
several calls) and whether a failed write fails the call (closed) or is only logged
(best effort).

| Use-case | Tool (`auth.require` / trust.approval) | AuditKind (`extra.op`) | Audit write |
|---|---|---|---|
| search | search | search | best effort |
| get_definition, definition_history, search_document, definition_references | get_definition | get_definition (op) | best effort |
| parse_query | parse_query (a build tool) | parse_query (SQL as SHA-256 only) | best effort |
| compile_and_run | compile_and_run; a final SELECT that reads raw tables (or table functions) also check_and_run (exploratory SQL) — the caller's `select` and the stored final SELECT of any composed definition used, verified or not, nested too (`_compose.require_exploratory`, `Compiled.reads_raw`) | compile_and_run | closed: written before rows return |
| check_and_run | check_and_run | check_and_run | closed: written before rows return |
| revalidate(ids) / revalidate() | compile_and_run / review_proposal with review_all (whole library) | revalidate | closed |
| propose_definition | propose_definition; owner rule (`_apply.check_owner`: any owner needs review_all) | propose (op) | closed |
| revise_definition, rename_definition | revise_definition; owner rule | propose (op; a rename also lists `old@v` and `new@v` and `extra.renamed_from/renamed_to`) | closed |
| propose_join (add / annotate) | propose_join | propose (op, `extra.action`) | closed |
| import_library | propose_definition | propose (op) | closed |
| list_proposals, get_proposal | list_proposals | review (op) | best effort |
| list_joins, describe_tables | the tool of the same name (pending proposals on edges only with list_proposals) | get_definition (op) | best effort |
| list_library | search; proposal rows/badges/statuses only with list_proposals; `needs_my_review` needs review_proposal (membership; four-eyes per row via can_approve) | search (op=list_library) | best effort |
| count_proposals | list_proposals | none (cheap badge poll) | — |
| approve, reject | review_proposal on target (review_all, or the definition's owner; joins need review_all; a VERIFIED target — or a join a person confirmed — needs a human or an admin agent); four-eyes (human admins exempt, agents never); approve(verify=True) also verify (never for joins or renames) | review (op) | closed |
| (open_context) schema-3 migration report | — (system, actor `cteforge`) | admin (op=migrate_adr_0026) | closed (opening fails, the note stays) |
| withdraw | the build tool of the proposal's kind, and the proposal's author | review (op) | closed |
| verify_definition | verify (humans whose role allows it; agents only with the admin role; owners may not); four-eyes over every author since the last logic change | review (op) | closed |
| list_audit | view_audit; then bad `kind` / naive `since` → PolicyError | admin (op=list_audit) | best effort |
| export_library | get_definition | admin (op) | closed |
| bootstrap_admin | none; only while no users exist | admin (op; token id + prefix) | closed |
| create_user | manage_user; `can_grant_role` (agents never grant admin); token only to a human caller | admin (op; token id + prefix) | closed |
| list_users, list_roles | list_users | admin (op) | best effort |
| update_user, set_role, set_active | manage_user; `can_manage_user` (agents never touch an admin user), `can_grant_role`; last active admin guard | admin (op) | closed |
| create_role, update_role, delete_role | manage_role; `can_edit_role` (agents never the admin role) | admin (op) | closed |
| me | none (the caller's own access) | none | — |
| list_tokens | own: none; another user's: list_users | admin (op=list_tokens) | best effort |
| create_token | never an agent caller; own: none, but a token-authenticated caller only within its own tools, expiry (none/later refused) and mode (`only` never mints `follow_role`), and the new token gets `parent_token_id`; another user's: manage_user (root-equivalent for humans) | admin (op; token id + prefix, never the value) | closed |
| revoke_token | own: none; another user's: manage_user + `can_manage_user`; revokes every descendant too | admin (op) | closed |
| set_token_tools | own: none (within the calling token's tools; `only` never switches to `follow_role`); another user's: manage_user + `can_manage_user` | admin (op) | closed |
| MCP call to a hidden tool (`auth.refuse_tool`) | — (refused) | admin (op=tool_refused, rejected) | best effort |
| get_settings, set_settings, set_default_connection | manage_settings | admin (op) | closed (get: best effort) |
| rebuild_search_index | manage_settings; meaning search off → PolicyError | admin (op=rebuild_search_index) | closed |

Outcome rule (`_audit.failure_outcome`, applied by `_audit.audit_failures`): OK on success;
REJECTED for PermissionDeniedError, PolicyError, StaleVersionError and for refusals returned
as outcomes (trust/strict mode, not read-only; check_and_run also counts SQL that does not
parse as refused); ERROR for any other failure (class name only). Never stored: SQL text
(SHA-256 only), result rows, tokens or token hashes (ids and 12-char prefixes only),
credentials.

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
